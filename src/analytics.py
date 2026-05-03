"""
Analytics queries for the Vinted scraper dataset.

All functions accept an asyncpg Pool directly and return list[dict].
On any SQL or connection error they log the exception and return [].

Timestamp convention
--------------------
Cutoff dates are computed in Python (UTC naive) and passed as $N parameters
so asyncpg handles the binding — no SQL interval arithmetic needed.

Median
------
All medians use PostgreSQL's  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ...)
which is the standard continuous-median aggregate (NULL-safe: NULLs ignored).

DOW convention for hourly_sales_heatmap
----------------------------------------
PostgreSQL EXTRACT(DOW) returns 0=Sunday … 6=Saturday.
We remap to 0=Monday … 6=Sunday with  (dow + 6) % 7  so the heatmap
follows the standard ISO week layout.
"""

import logging
from datetime import datetime, timedelta, timezone

import asyncpg

logger = logging.getLogger(__name__)


# ── Internal helpers ──────────────────────────────────────────────────────

def _cutoff(days: int) -> datetime:
    """Return a UTC-naive datetime `days` ago (matches asyncpg TIMESTAMP columns)."""
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


def _rows(records) -> list[dict]:
    """Convert asyncpg Record list to plain dicts, rounding floats to 2dp."""
    result = []
    for r in records:
        row = {}
        for k, v in r.items():
            if isinstance(v, float):
                v = round(v, 2)
            row[k] = v
        result.append(row)
    return result


async def _fetch(pool: asyncpg.Pool, sql: str, *args, label: str) -> list[dict]:
    """Execute a SELECT and return list[dict], logging on error."""
    try:
        async with pool.acquire() as conn:
            records = await conn.fetch(sql, *args)
        return _rows(records)
    except Exception as exc:
        logger.error("analytics.%s failed: %s", label, exc)
        return []


# ── Public API ────────────────────────────────────────────────────────────

async def velocity_by_category(
    pool: asyncpg.Pool,
    days: int = 30,
) -> list[dict]:
    """
    Per category (sold in the last N days):
      - sold_count
      - median_hours_to_sell   (vinted_created_at → sold_at)
      - median_price

    Rows without vinted_created_at or with negative durations are excluded
    from the velocity median but still counted.
    """
    cutoff = _cutoff(days)
    sql = """
        SELECT
            category,
            COUNT(*)                                                     AS sold_count,
            PERCENTILE_CONT(0.5) WITHIN GROUP (
                ORDER BY
                    EXTRACT(EPOCH FROM (sold_at - vinted_created_at)) / 3600.0
            ) FILTER (
                WHERE vinted_created_at IS NOT NULL
                  AND sold_at > vinted_created_at
            )                                                            AS median_hours_to_sell,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price)          AS median_price
        FROM   articles
        WHERE  status   = 'sold'
          AND  sold_at >= $1
          AND  category IS NOT NULL
          AND  category != ''
        GROUP  BY category
        ORDER  BY sold_count DESC
    """
    return await _fetch(pool, sql, cutoff, label="velocity_by_category")


async def velocity_by_brand(
    pool: asyncpg.Pool,
    category: str | None = None,
    days: int = 30,
) -> list[dict]:
    """
    Per brand (sold in the last N days), optionally filtered to one category:
      - sold_count
      - median_hours_to_sell
      - median_price

    Brands that are empty / NULL are excluded.
    """
    cutoff = _cutoff(days)

    if category:
        sql = """
            SELECT
                brand,
                COUNT(*)                                                     AS sold_count,
                PERCENTILE_CONT(0.5) WITHIN GROUP (
                    ORDER BY
                        EXTRACT(EPOCH FROM (sold_at - vinted_created_at)) / 3600.0
                ) FILTER (
                    WHERE vinted_created_at IS NOT NULL
                      AND sold_at > vinted_created_at
                )                                                            AS median_hours_to_sell,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price)          AS median_price
            FROM   articles
            WHERE  status   = 'sold'
              AND  sold_at >= $1
              AND  category = $2
              AND  brand IS NOT NULL
              AND  brand != ''
            GROUP  BY brand
            ORDER  BY sold_count DESC
        """
        return await _fetch(pool, sql, cutoff, category, label="velocity_by_brand")
    else:
        sql = """
            SELECT
                brand,
                COUNT(*)                                                     AS sold_count,
                PERCENTILE_CONT(0.5) WITHIN GROUP (
                    ORDER BY
                        EXTRACT(EPOCH FROM (sold_at - vinted_created_at)) / 3600.0
                ) FILTER (
                    WHERE vinted_created_at IS NOT NULL
                      AND sold_at > vinted_created_at
                )                                                            AS median_hours_to_sell,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price)          AS median_price
            FROM   articles
            WHERE  status   = 'sold'
              AND  sold_at >= $1
              AND  brand IS NOT NULL
              AND  brand != ''
            GROUP  BY brand
            ORDER  BY sold_count DESC
        """
        return await _fetch(pool, sql, cutoff, label="velocity_by_brand")


async def price_vs_velocity(
    pool: asyncpg.Pool,
    category: str,
    days: int = 30,
) -> list[dict]:
    """
    For a given category, group sold articles into price buckets and return:
      - price_bucket   (label: '0-10', '10-20', …, '100+')
      - sold_count
      - median_hours_to_sell

    Buckets are ordered from cheapest to most expensive.
    Articles without vinted_created_at or negative durations are counted
    but excluded from the velocity median.
    """
    cutoff = _cutoff(days)
    sql = """
        SELECT
            CASE
                WHEN price <   10 THEN '0-10'
                WHEN price <   20 THEN '10-20'
                WHEN price <   30 THEN '20-30'
                WHEN price <   50 THEN '30-50'
                WHEN price <  100 THEN '50-100'
                ELSE '100+'
            END                                                          AS price_bucket,
            CASE
                WHEN price <   10 THEN 0
                WHEN price <   20 THEN 1
                WHEN price <   30 THEN 2
                WHEN price <   50 THEN 3
                WHEN price <  100 THEN 4
                ELSE 5
            END                                                          AS bucket_order,
            COUNT(*)                                                     AS sold_count,
            PERCENTILE_CONT(0.5) WITHIN GROUP (
                ORDER BY
                    EXTRACT(EPOCH FROM (sold_at - vinted_created_at)) / 3600.0
            ) FILTER (
                WHERE vinted_created_at IS NOT NULL
                  AND sold_at > vinted_created_at
            )                                                            AS median_hours_to_sell
        FROM   articles
        WHERE  status   = 'sold'
          AND  category = $1
          AND  sold_at >= $2
          AND  price IS NOT NULL
        GROUP  BY price_bucket, bucket_order
        ORDER  BY bucket_order
    """
    return await _fetch(pool, sql, category, cutoff, label="price_vs_velocity")


async def sell_through_rate(
    pool: asyncpg.Pool,
    days: int = 7,
) -> list[dict]:
    """
    Per category, among articles first published in the last N days:
      - total_published   (all statuses)
      - sold_count
      - sell_through_pct  (sold / total × 100, rounded to 1 dp)

    "Published" = COALESCE(vinted_created_at, first_seen_at).
    Categories with zero articles are excluded.
    Results ordered by sell_through_pct DESC.
    """
    cutoff = _cutoff(days)
    sql = """
        SELECT
            category,
            COUNT(*)                                                          AS total_published,
            COUNT(*) FILTER (WHERE status = 'sold')                          AS sold_count,
            ROUND(
                100.0
                * COUNT(*) FILTER (WHERE status = 'sold')
                / NULLIF(COUNT(*), 0),
                1
            )                                                                 AS sell_through_pct
        FROM   articles
        WHERE  COALESCE(vinted_created_at, first_seen_at) >= $1
          AND  category IS NOT NULL
          AND  category != ''
        GROUP  BY category
        HAVING COUNT(*) > 0
        ORDER  BY sell_through_pct DESC NULLS LAST,
                  total_published  DESC
    """
    return await _fetch(pool, sql, cutoff, label="sell_through_rate")


async def hourly_sales_heatmap(
    pool: asyncpg.Pool,
    days: int = 30,
) -> list[dict]:
    """
    Aggregate sold_at by hour-of-day (0–23) and day-of-week (0=Mon … 6=Sun)
    to identify the hours/days with the most sales.

    Returns rows: { weekday: int, hour: int, sales_count: int }
    Ordered by weekday ASC, hour ASC — ready for a 7×24 heatmap grid.

    Missing (weekday, hour) combinations are NOT returned (no zero-fill);
    the caller should treat absent cells as 0.
    """
    cutoff = _cutoff(days)
    sql = """
        SELECT
            ((EXTRACT(DOW FROM sold_at)::int + 6) % 7)   AS weekday,
            EXTRACT(HOUR FROM sold_at)::int               AS hour,
            COUNT(*)                                      AS sales_count
        FROM   articles
        WHERE  status  = 'sold'
          AND  sold_at >= $1
          AND  sold_at IS NOT NULL
        GROUP  BY weekday, hour
        ORDER  BY weekday, hour
    """
    return await _fetch(pool, sql, cutoff, label="hourly_sales_heatmap")
