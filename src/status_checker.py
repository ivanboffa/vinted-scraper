"""
Status checker — verifies whether tracked articles are still active on Vinted.

For each active article in the DB it calls GET /api/v2/items/{id} and updates
the lifecycle status (sold / deleted) accordingly.

Articles are checked in priority order:
  1. fresh   — first_seen_at < 24 h ago   (sell quickly → check often)
  2. recent  — first_seen_at 1–7 days ago
  3. old     — first_seen_at > 7 days ago
"""

import asyncio
import logging
import random
import time
from datetime import datetime, timedelta, timezone

from .api_client import VintedAPIClient
from .db_manager import AsyncDatabaseManager

logger = logging.getLogger(__name__)

# ── Tuning ────────────────────────────────────────────────────────────────
_CONCURRENCY   = 3      # max simultaneous item-check requests
_DELAY_BASE    = 2.0    # seconds between requests (per worker)
_DELAY_SPREAD  = 0.4    # ± jitter applied on top of base delay

_AGE_FRESH     = timedelta(hours=24)
_AGE_RECENT    = timedelta(days=7)


# ── Helpers ───────────────────────────────────────────────────────────────

def _now_naive() -> datetime:
    """Return the current UTC time as a naive datetime (matches asyncpg TIMESTAMP)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _age_buckets(items: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Split items into three age buckets based on first_seen_at.
    Returns (fresh, recent, old) — each is a list of item dicts.
    """
    now = _now_naive()
    fresh, recent, old = [], [], []
    for item in items:
        fsa = item.get("first_seen_at")
        # asyncpg may return tz-aware or naive datetimes; normalise to naive UTC
        if fsa is None:
            old.append(item)
            continue
        if isinstance(fsa, datetime) and fsa.tzinfo is not None:
            fsa = fsa.astimezone(timezone.utc).replace(tzinfo=None)
        age = now - fsa
        if age < _AGE_FRESH:
            fresh.append(item)
        elif age < _AGE_RECENT:
            recent.append(item)
        else:
            old.append(item)
    return fresh, recent, old


def _parse_sold_status(body: dict) -> str:
    """
    Analyse a 200-OK item body.
    Returns one of: 'sold', 'deleted', 'active'.

    Vinted uses different fields depending on API version / item type:
      - is_sold: bool
      - is_closed: bool or int (0/1)
      - item_closing_action: "sold" | "deleted" | null
      - status: "sold" | "active" | "reserved" | ...
      - can_buy: bool (false = unavailable, but not necessarily sold)
    """
    item = body.get("item") or body  # some responses wrap, some don't

    is_sold         = item.get("is_sold", False)
    is_closed       = item.get("is_closed", False)
    closing_action  = (item.get("item_closing_action") or "").lower().strip()
    item_status     = (item.get("status") or "").lower().strip()
    can_buy         = item.get("can_buy", True)   # False = can't purchase

    # Sold: any authoritative sold signal
    if is_sold:
        return "sold"
    if item_status == "sold":
        return "sold"
    if is_closed and closing_action == "sold":
        return "sold"
    # can_buy=False alone is not conclusive (reserved items are also not buyable),
    # but combined with is_closed it is.
    if is_closed and not can_buy and closing_action in ("", "sold"):
        return "sold"

    # Deleted: closed but not for a "sold" reason
    if is_closed:
        return "deleted"

    return "active"


# ── Core worker ───────────────────────────────────────────────────────────

async def _check_one(
    vinted_id: str,
    client: VintedAPIClient,
    db: AsyncDatabaseManager,
    semaphore: asyncio.Semaphore,
    stats: dict,
    delay: float = _DELAY_BASE,
) -> None:
    """Check a single item via web scraping and update the DB.

    Uses check_item_web (HTML page) instead of the individual item API
    (GET /api/v2/items/{id}), which is heavily rate-limited/blocked from
    GitHub Actions IP ranges (88% curl timeout rate observed).
    Web scraping has ~74% success rate vs ~12% for the API.
    """
    async with semaphore:
        actual_delay = delay + random.uniform(-_DELAY_SPREAD, _DELAY_SPREAD)
        await asyncio.sleep(max(0.3, actual_delay))
        web_status, views, favourites, created_at, country = await client.check_item_web(vinted_id)

    if web_status == "unknown":
        # Web page rate-limited or unparseable → skip, retry at next run
        stats["errors"] += 1
        return

    stats["checked"] += 1

    if web_status == "sold":
        await db.mark_as_sold(vinted_id)
        stats["sold"] += 1
        logger.info("SOLD (web) %s", vinted_id)
    elif web_status == "deleted":
        await db.mark_as_deleted(vinted_id)
        stats["deleted"] += 1
        logger.info("DELETED (web) %s", vinted_id)
    # web_status == "active" → still listed, no DB update needed

    if any(v is not None for v in (views, favourites, created_at, country)):
        await db.update_item_details(
            vinted_id,
            views=views,
            favourites=favourites,
            vinted_created_at=created_at,
            country_iso_code=country,
        )
        stats["engagement_updated"] += 1


async def _check_bucket(
    bucket: list[dict],
    label: str,
    client: VintedAPIClient,
    db: AsyncDatabaseManager,
    semaphore: asyncio.Semaphore,
    stats: dict,
    delay: float = _DELAY_BASE,
) -> None:
    """Run checks for one age bucket, logging progress."""
    if not bucket:
        return
    logger.info("Status check — %s bucket: %d items", label, len(bucket))
    tasks = [
        asyncio.create_task(_check_one(item["vinted_id"], client, db, semaphore, stats, delay=delay))
        for item in bucket
    ]
    await asyncio.gather(*tasks, return_exceptions=True)


# ── Public entry point ────────────────────────────────────────────────────

async def run_status_check(
    client: VintedAPIClient,
    db: AsyncDatabaseManager,
    limit: int = 2000,
    oldest_first: bool = False,
    concurrency: int = _CONCURRENCY,
    delay: float = _DELAY_BASE,
    fresh_only: bool = False,
    fresh_hours: int = 48,
    min_age_hours: int = 0,
) -> dict:
    """
    Check active articles for sold/deleted status.

    Args:
        client:        existing VintedAPIClient (must be inside its async context).
        db:            existing AsyncDatabaseManager (must be connected).
        limit:         max number of active articles to check per run.
        oldest_first:  if True, check oldest articles first (most likely gone);
                       if False (default), check newest first (may sell quickly).
        concurrency:   max simultaneous item-check requests (overrides _CONCURRENCY).
        delay:         base seconds between requests per worker (overrides _DELAY_BASE).
        fresh_only:    if True, only check articles seen within fresh_hours hours.
        fresh_hours:   age threshold for fresh_only filter (default 48h).
        min_age_hours: if > 0, skip articles newer than this many hours
                       (used by mid-check to target the 48h–7d age band).

    Returns:
        {
            "checked":            int,
            "sold":               int,
            "deleted":            int,
            "errors":             int,
            "engagement_updated": int,
            "duration_seconds":   float,
        }
    """
    t0 = time.monotonic()
    stats: dict = {"checked": 0, "sold": 0, "deleted": 0, "errors": 0, "engagement_updated": 0}

    items = await db.get_active_items(
        limit=limit,
        oldest_first=oldest_first,
        fresh_only=fresh_only,
        fresh_hours=fresh_hours,
        min_age_hours=min_age_hours,
    )
    if not items:
        logger.info("Status check — no active items to check.")
        stats["duration_seconds"] = round(time.monotonic() - t0, 2)
        return stats

    logger.info(
        "Status check — %d active items fetched (limit=%d, oldest_first=%s, fresh_only=%s, min_age_hours=%d, concurrency=%d, delay=%.1fs)",
        len(items), limit, oldest_first, fresh_only, min_age_hours, concurrency, delay,
    )

    fresh, recent, old = _age_buckets(items)
    logger.info(
        "Buckets — fresh: %d | recent: %d | old: %d",
        len(fresh), len(recent), len(old),
    )

    # One shared semaphore across all buckets
    semaphore = asyncio.Semaphore(concurrency)

    # Process buckets in priority order — await each so fresh items finish first
    for bucket, label in [(fresh, "fresh"), (recent, "recent"), (old, "old")]:
        await _check_bucket(bucket, label, client, db, semaphore, stats, delay=delay)

    stats["duration_seconds"] = round(time.monotonic() - t0, 2)
    logger.info(
        "Status check done in %.1fs — checked=%d sold=%d deleted=%d errors=%d",
        stats["duration_seconds"],
        stats["checked"], stats["sold"], stats["deleted"], stats["errors"],
    )
    return stats
