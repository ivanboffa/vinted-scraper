import logging
import ssl as ssl_module
import asyncpg
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS articles (
    id                SERIAL PRIMARY KEY,
    vinted_id         TEXT UNIQUE NOT NULL,
    title             TEXT,
    price             NUMERIC,
    currency          TEXT,
    url               TEXT,
    image_url         TEXT,
    brand             TEXT,
    size              TEXT,
    condition         TEXT,
    category          TEXT,

    -- lifecycle
    status            TEXT        NOT NULL DEFAULT 'active',  -- 'active' | 'sold' | 'deleted'
    first_seen_at     TIMESTAMP   NOT NULL DEFAULT NOW(),      -- never overwritten after insert
    last_seen_at      TIMESTAMP   NOT NULL DEFAULT NOW(),      -- updated on every scrape
    sold_at           TIMESTAMP   NULL,                        -- set by status checker, never by scraper
    vinted_created_at TIMESTAMP   NULL,                        -- original publication timestamp from Vinted

    -- seller / location
    seller_id                  TEXT        NULL,
    seller_item_count          INT         NULL,
    seller_feedback_count      INT         NULL,
    seller_feedback_reputation TEXT        NULL,
    country_iso_code           TEXT        NULL,               -- ISO-3166-1 alpha-2 country of seller (IT, FR, DE…)

    -- engagement
    photo_count       INT         NULL,
    views_count       INT         NULL,
    favourite_count   INT         NULL,

    -- listing content
    description       TEXT        NULL
);

CREATE INDEX IF NOT EXISTS idx_articles_category        ON articles (category);
CREATE INDEX IF NOT EXISTS idx_articles_brand           ON articles (brand);
CREATE INDEX IF NOT EXISTS idx_articles_price           ON articles (price);
CREATE INDEX IF NOT EXISTS idx_articles_condition       ON articles (condition);
CREATE INDEX IF NOT EXISTS idx_articles_status          ON articles (status);
CREATE INDEX IF NOT EXISTS idx_articles_first_seen_at   ON articles (first_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_last_seen_at    ON articles (last_seen_at DESC);
"""

# Migration: add new columns to existing tables that pre-date this schema version.
MIGRATE_SQL = """
ALTER TABLE articles ADD COLUMN IF NOT EXISTS country_iso_code           TEXT NULL;
ALTER TABLE articles ADD COLUMN IF NOT EXISTS seller_id                  TEXT NULL;
ALTER TABLE articles ADD COLUMN IF NOT EXISTS seller_item_count          INT  NULL;
ALTER TABLE articles ADD COLUMN IF NOT EXISTS seller_feedback_count      INT  NULL;
ALTER TABLE articles ADD COLUMN IF NOT EXISTS seller_feedback_reputation TEXT NULL;
ALTER TABLE articles ADD COLUMN IF NOT EXISTS description                TEXT NULL;
CREATE INDEX IF NOT EXISTS idx_articles_country        ON articles (country_iso_code);
CREATE INDEX IF NOT EXISTS idx_articles_vinted_created ON articles (vinted_created_at DESC);
"""

# On INSERT  → set first_seen_at, last_seen_at, status = 'active'
# On CONFLICT → update only scraped fields + last_seen_at
#               NEVER touch first_seen_at, sold_at, status
UPSERT_SQL = """
INSERT INTO articles (
    vinted_id, title, price, currency, url, image_url,
    brand, size, condition, category,
    vinted_created_at, photo_count, views_count, favourite_count,
    seller_id, seller_item_count, seller_feedback_count, seller_feedback_reputation,
    country_iso_code, description,
    status, first_seen_at, last_seen_at
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
        $15, $16, $17, $18, $19, $20,
        'active', NOW(), NOW())
ON CONFLICT (vinted_id) DO UPDATE SET
    title                      = EXCLUDED.title,
    price                      = EXCLUDED.price,
    image_url                  = EXCLUDED.image_url,
    brand                      = EXCLUDED.brand,
    size                       = EXCLUDED.size,
    condition                  = EXCLUDED.condition,
    category                   = EXCLUDED.category,
    vinted_created_at          = COALESCE(EXCLUDED.vinted_created_at,          articles.vinted_created_at),
    photo_count                = COALESCE(EXCLUDED.photo_count,                articles.photo_count),
    favourite_count            = COALESCE(EXCLUDED.favourite_count,            articles.favourite_count),
    seller_id                  = COALESCE(EXCLUDED.seller_id,                  articles.seller_id),
    seller_item_count          = COALESCE(EXCLUDED.seller_item_count,          articles.seller_item_count),
    seller_feedback_count      = COALESCE(EXCLUDED.seller_feedback_count,      articles.seller_feedback_count),
    seller_feedback_reputation = COALESCE(EXCLUDED.seller_feedback_reputation, articles.seller_feedback_reputation),
    country_iso_code           = COALESCE(EXCLUDED.country_iso_code,           articles.country_iso_code),
    description                = COALESCE(EXCLUDED.description,                articles.description),
    last_seen_at               = NOW()
    -- first_seen_at, sold_at, status, views_count: intentionally NOT touched here
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_sql(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]


def _make_ssl(require: bool):
    if not require:
        return None
    ctx = ssl_module.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl_module.CERT_NONE
    return ctx


# ---------------------------------------------------------------------------
# Async manager (primary)
# ---------------------------------------------------------------------------

class AsyncDatabaseManager:
    """asyncpg connection-pool manager with article lifecycle support."""

    def __init__(self, database_url: str, ssl: bool = True):
        self.database_url = database_url
        self._ssl = ssl
        self._pool: asyncpg.Pool | None = None

    async def connect(self):
        self._pool = await asyncpg.create_pool(
            self.database_url,
            min_size=2,
            max_size=10,
            command_timeout=60,
            ssl=_make_ssl(self._ssl),
        )
        async with self._pool.acquire() as conn:
            for stmt in _split_sql(CREATE_TABLE_SQL):
                await conn.execute(stmt)
            # Apply any additive migrations for existing DBs
            for stmt in _split_sql(MIGRATE_SQL):
                try:
                    await conn.execute(stmt)
                except Exception:
                    pass  # index/column may already exist
        logger.info("Database pool created and schema initialized.")

    # ------------------------------------------------------------------
    # Bulk write
    # ------------------------------------------------------------------

    async def insert_articles_batch(self, items: list[dict]) -> int:
        """Upsert a batch of scraped article dicts. Returns count saved."""
        if not items or self._pool is None:
            return 0

        rows = [
            (
                item.get("vinted_id"),
                item.get("title"),
                item.get("price"),
                item.get("currency", "EUR"),
                item.get("url"),
                item.get("image_url"),
                item.get("brand", ""),
                item.get("size", ""),
                item.get("condition", ""),
                item.get("category", ""),
                item.get("vinted_created_at"),             # TIMESTAMP or None
                item.get("photo_count"),                   # INT or None
                item.get("views_count"),                   # INT or None
                item.get("favourite_count"),               # INT or None
                item.get("seller_id"),                     # TEXT or None
                item.get("seller_item_count"),             # INT or None
                item.get("seller_feedback_count"),         # INT or None
                item.get("seller_feedback_reputation"),    # TEXT or None
                item.get("country_iso_code"),              # TEXT or None e.g. "IT"
                item.get("description"),                   # TEXT or None
            )
            for item in items
        ]

        try:
            async with self._pool.acquire() as conn:
                await conn.executemany(UPSERT_SQL, rows)
            return len(rows)
        except Exception as exc:
            logger.error("Batch insert error: %s", exc)
            return 0

    # ------------------------------------------------------------------
    # Lifecycle reads
    # ------------------------------------------------------------------

    async def get_active_items(self, limit: int = 1000, oldest_first: bool = False) -> list[dict]:
        """Return active articles ordered by first_seen_at (DESC=newest, ASC=oldest)."""
        if self._pool is None:
            return []
        order = "ASC" if oldest_first else "DESC"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT *
                FROM   articles
                WHERE  status = 'active'
                ORDER  BY first_seen_at {order}
                LIMIT  $1
                """,
                limit,
            )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Lifecycle writes  (called by the status-checker, not the scraper)
    # ------------------------------------------------------------------

    async def mark_as_sold(self, vinted_id: str) -> None:
        """Mark an article as sold: set sold_at = NOW(), status = 'sold'."""
        if self._pool is None:
            return
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE articles
                SET    sold_at  = NOW(),
                       status   = 'sold'
                WHERE  vinted_id = $1
                  AND  status   != 'sold'
                """,
                vinted_id,
            )
        logger.debug("mark_as_sold %s — %s", vinted_id, result)

    async def mark_as_deleted(self, vinted_id: str) -> None:
        """Mark an article as deleted (removed from Vinted, not sold)."""
        if self._pool is None:
            return
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE articles
                SET    status = 'deleted'
                WHERE  vinted_id = $1
                  AND  status    != 'sold'
                """,
                vinted_id,
            )
        logger.debug("mark_as_deleted %s — %s", vinted_id, result)

    async def reactivate(self, vinted_id: str) -> None:
        """Riporta un articolo a status='active' (es. rimesso in vendita dal seller)."""
        if self._pool is None:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE articles
                    SET    status = 'active', last_seen_at = NOW()
                    WHERE  vinted_id = $1 AND status = 'deleted'
                    """,
                    vinted_id,
                )
        except Exception as exc:
            logger.error("reactivate %s failed: %s", vinted_id, exc)

    async def update_engagement(
        self,
        vinted_id: str,
        views: int | None,
        favourites: int | None,
    ) -> None:
        """Update views_count and favourite_count from the individual item endpoint."""
        if self._pool is None:
            return
        if views is None and favourites is None:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE articles
                    SET    views_count     = COALESCE($2, views_count),
                           favourite_count = COALESCE($3, favourite_count)
                    WHERE  vinted_id = $1
                    """,
                    vinted_id,
                    views,
                    favourites,
                )
        except Exception as exc:
            logger.error("update_engagement %s failed: %s", vinted_id, exc)

    async def update_item_details(
        self,
        vinted_id: str,
        *,
        views: int | None = None,
        favourites: int | None = None,
        vinted_created_at=None,
        country_iso_code: str | None = None,
    ) -> None:
        """
        Update enrichment fields collected from the individual item API or web page:
        views, favourites, original publication date, seller country.
        Only non-None values are written (existing data is never overwritten with NULL).
        """
        if self._pool is None:
            return
        if all(v is None for v in (views, favourites, vinted_created_at, country_iso_code)):
            return
        # asyncpg requires naive (tz-unaware) datetimes for TIMESTAMP WITHOUT TIME ZONE columns
        if vinted_created_at is not None and hasattr(vinted_created_at, "tzinfo") and vinted_created_at.tzinfo is not None:
            from datetime import timezone
            vinted_created_at = vinted_created_at.astimezone(timezone.utc).replace(tzinfo=None)
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE articles
                    SET    views_count       = COALESCE($2, views_count),
                           favourite_count   = COALESCE($3, favourite_count),
                           vinted_created_at = COALESCE($4, vinted_created_at),
                           country_iso_code  = COALESCE($5, country_iso_code)
                    WHERE  vinted_id = $1
                    """,
                    vinted_id,
                    views,
                    favourites,
                    vinted_created_at,
                    country_iso_code,
                )
        except Exception as exc:
            logger.error("update_item_details %s failed: %s", vinted_id, exc)

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    async def close(self):
        if self._pool:
            await self._pool.close()
            logger.info("Database pool closed.")


# ---------------------------------------------------------------------------
# Sync fallback (local testing / migrations)
# ---------------------------------------------------------------------------

class DatabaseManager:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.conn = None
        self.cursor = None

    def connect(self):
        self.conn = psycopg2.connect(self.database_url)
        self.cursor = self.conn.cursor(cursor_factory=RealDictCursor)
        for stmt in _split_sql(CREATE_TABLE_SQL):
            self.cursor.execute(stmt)
        self.conn.commit()

    def close(self):
        if self.cursor:
            self.cursor.close()
        if self.conn:
            self.conn.close()
