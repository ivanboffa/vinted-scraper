import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MAX_PAGES_PER_CATEGORY = 10   # Vinted API hard-caps total_pages at 10 (~960 items/category)
ITEMS_PER_PAGE = 96
DB_BATCH_SIZE = 500

# Maps Vinted status IDs to human-readable Italian condition labels
_CONDITION_MAP: dict[int, str] = {
    1: "nuovo con etichetta",
    2: "nuovo senza etichetta",
    3: "ottimo",
    4: "buono",
    5: "soddisfacente",
}


@dataclass
class ScrapeStats:
    categories_total: int = 0
    categories_done: int = 0
    pages_fetched: int = 0
    items_found: int = 0
    items_saved: int = 0
    errors: list[str] = field(default_factory=list)
    _MAX_ERRORS = 200   # cap to avoid unbounded memory growth

    def add_error(self, msg: str) -> None:
        if len(self.errors) < self._MAX_ERRORS:
            self.errors.append(msg)

    def summary(self) -> str:
        return (
            f"Categories: {self.categories_done}/{self.categories_total} | "
            f"Pages: {self.pages_fetched} | "
            f"Items found: {self.items_found} | "
            f"Saved: {self.items_saved} | "
            f"Errors: {len(self.errors)}"
        )


def _parse_ts(value) -> datetime | None:
    """
    Convert a Vinted timestamp to a naive UTC datetime (no tzinfo),
    compatible with asyncpg TIMESTAMP WITHOUT TIME ZONE columns.
    Accepts:
      - int / float  → Unix epoch seconds
      - str (digits) → Unix epoch seconds
      - str (ISO)    → parsed via fromisoformat (e.g. "2024-04-24T23:06:40+00:00")
    """
    if value is None:
        return None
    # Numeric or digit-only string → Unix timestamp
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().lstrip("-").isdigit()):
        try:
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
            return dt.replace(tzinfo=None)   # naive UTC
        except (TypeError, ValueError, OSError):
            return None
    # ISO 8601 string
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return dt   # already naive
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _parse_item(raw: dict, category_path: str) -> dict | None:
    try:
        # Validate required field first — everything else is optional
        if not raw.get("id"):
            return None

        # Image URL — "photo" (singular) is the fallback field name, no default {}
        # so that a truly absent field hits the else branch → photo_count = 0
        photos = raw.get("photos") or raw.get("photo")
        if isinstance(photos, list):
            image_url = (photos[0].get("full_size_url") or photos[0].get("url")) if photos else None
            photo_count = len(photos)
        elif isinstance(photos, dict):
            image_url = photos.get("full_size_url") or photos.get("url")
            photo_count = 1 if image_url else 0
        else:
            # photos absent or None → spec says photo_count = 0
            image_url = None
            photo_count = 0

        # Price — Vinted returns price as a dict {'amount': '5.0', 'currency_code': 'EUR'}
        # or occasionally as a bare numeric / string.  We always resolve to a float.
        price_field = raw.get("price")
        if isinstance(price_field, dict):
            price_raw = price_field.get("amount") or 0
        else:
            price_raw = price_field if price_field is not None else raw.get("price_numeric", 0)
        try:
            price = float(str(price_raw).replace(",", ".")) if price_raw not in (None, "") else None
        except (ValueError, TypeError):
            price = None

        # Condition — prefer human label, fall back to status_id lookup
        condition = raw.get("status") or raw.get("condition") or ""
        if not condition:
            status_id = raw.get("status_id") or raw.get("condition_id")
            condition = _CONDITION_MAP.get(status_id, "") if status_id else ""

        # Vinted publication timestamp (Unix epoch or ISO, several possible field names)
        vinted_created_at = _parse_ts(
            raw.get("created_at_ts")
            or raw.get("created_at")
        )

        # Seller info (nested under "user" object, with fallback flat fields)
        user = raw.get("user") or {}
        raw_seller_id = user.get("id") or raw.get("user_id")
        seller_id = str(raw_seller_id) if raw_seller_id is not None else None

        # Country: try item-level, then seller's user object, then nested location
        country_iso_code = (
            raw.get("country_iso_code")
            or user.get("country_iso_code")
            or (user.get("country") or {}).get("iso_code")
            or (user.get("location") or {}).get("iso_code")
        )
        if country_iso_code:
            country_iso_code = str(country_iso_code).upper()[:2]

        return {
            "vinted_id":          str(raw["id"]),
            "title":              raw.get("title"),
            "price":              price,
            "currency":           (price_field.get("currency_code") if isinstance(price_field, dict) else None) or raw.get("currency") or "EUR",
            "url":                raw.get("url") or f"https://www.vinted.it/items/{raw['id']}",
            "image_url":          image_url,
            "brand":              raw.get("brand_title") or raw.get("brand") or "",
            "size":               raw.get("size_title") or raw.get("size") or "",
            "condition":          condition,
            "category":           category_path,
            # lifecycle
            "vinted_created_at":  vinted_created_at,
            # engagement — use explicit None check so that 0 is kept as 0, not dropped
            "photo_count":        photo_count,
            "views_count":        raw["view_count"]       if "view_count"       in raw else raw.get("views_count"),
            "favourite_count":    raw["favourite_count"]  if "favourite_count"  in raw else raw.get("favorites_count"),
            # seller
            "seller_id":          seller_id,
            "seller_item_count":  user.get("items_count") or None,
            "seller_feedback_count":      user.get("feedback_count") or None,
            "seller_feedback_reputation": user.get("feedback_reputation") or None,
            # description
            "description":        raw.get("description") or None,
            # location
            "country_iso_code":   country_iso_code or None,
        }
    except Exception as exc:
        logger.debug("Failed to parse item %s: %s", raw.get("id"), exc)
        return None


async def _scrape_category(
    client,
    catalog_id: int,
    category_path: str,
    queue: asyncio.Queue,
    stats: ScrapeStats,
):
    page = 1
    while page <= MAX_PAGES_PER_CATEGORY:
        try:
            data = await client.get_items_page(catalog_id, page=page, per_page=ITEMS_PER_PAGE)
        except Exception as exc:
            msg = f"catalog {catalog_id} p{page}: {exc}"
            logger.error(msg)
            stats.add_error(msg)
            break

        if not data:
            break

        raw_items = data.get("items", [])
        if not raw_items:
            break

        batch = []
        for raw in raw_items:
            item = _parse_item(raw, category_path)
            if item:
                batch.append(item)
            else:
                stats.add_error(f"parse_fail catalog={catalog_id} id={raw.get('id')}")

        if batch:
            await queue.put(batch)
            stats.items_found += len(batch)

        stats.pages_fetched += 1

        pagination = data.get("pagination", {})
        total_pages = pagination.get("total_pages", 1)
        if page >= total_pages:
            break
        page += 1

    stats.categories_done += 1
    logger.info("[%d/%d] %s done", stats.categories_done, stats.categories_total, category_path)


async def _db_worker(db, queue: asyncio.Queue, stats: ScrapeStats, stop_event: asyncio.Event):
    pending: list[dict] = []
    _consecutive_failures = 0
    _MAX_FAILURES = 5   # drop batch after this many consecutive DB errors

    async def flush():
        nonlocal _consecutive_failures
        if not pending:
            return
        try:
            saved = await db.insert_articles_batch(list(pending))
            stats.items_saved += saved
            pending.clear()
            _consecutive_failures = 0
        except Exception as exc:
            _consecutive_failures += 1
            stats.add_error(f"db_flush: {exc}")
            logger.error("DB flush error (%d/%d): %s", _consecutive_failures, _MAX_FAILURES, exc)
            if _consecutive_failures >= _MAX_FAILURES:
                logger.critical(
                    "DB flush failed %d times in a row — dropping %d items to prevent memory growth",
                    _MAX_FAILURES, len(pending),
                )
                pending.clear()
                _consecutive_failures = 0

    while not stop_event.is_set() or not queue.empty():
        try:
            batch = queue.get_nowait()
            pending.extend(batch)
            queue.task_done()
        except asyncio.QueueEmpty:
            await flush()
            await asyncio.sleep(0.5)
            continue

        if len(pending) >= DB_BATCH_SIZE:
            await flush()

    await flush()


async def _staggered_scrape(
    client, catalog_id: int, category_path: str,
    queue: asyncio.Queue, stats: ScrapeStats, start_delay: float,
):
    """Wrap _scrape_category with an initial delay to avoid request bursts."""
    await asyncio.sleep(start_delay)
    await _scrape_category(client, catalog_id, category_path, queue, stats)


async def run_full_scrape(client, db, categories: list[tuple[int, str]]) -> ScrapeStats:
    stats = ScrapeStats(categories_total=len(categories))
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    stop_event = asyncio.Event()

    db_task = asyncio.create_task(_db_worker(db, queue, stats, stop_event))

    # Stagger category starts: 1.5s apart so the server never sees a burst of
    # simultaneous identical requests, which is a clear bot pattern.
    scrape_tasks = [
        asyncio.create_task(
            _staggered_scrape(client, cid, path, queue, stats, start_delay=i * 1.5)
        )
        for i, (cid, path) in enumerate(categories)
    ]

    try:
        await asyncio.gather(*scrape_tasks, return_exceptions=True)
    finally:
        # Always signal the DB worker to stop and wait for it to flush,
        # even if this coroutine is cancelled mid-scrape.
        stop_event.set()
        await db_task

    logger.info("Scrape complete: %s", stats.summary())
    return stats
