"""
Run scrape + status-check cycles until the DB reaches 200k total articles.
Progress is printed after every cycle.
"""
import asyncio
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

import config
from src.api_client import VintedAPIClient
from src.async_scraper import run_full_scrape
from src.categories import fetch_all_categories, filter_categories
from src.db_manager import AsyncDatabaseManager
from src.status_checker import run_status_check

TARGET = 200_000
BLOCKED_WAIT_S = 3600   # 1 hour between retries when IP-blocked
_PROBE_CAT    = 4       # category to use for quick health probe


async def _api_is_alive(client) -> bool:
    """Quick probe: fetch 1 item from catalog 4. Returns True if API responds."""
    data = await client.get_items_page(_PROBE_CAT, page=1, per_page=1, retries=1)
    return bool(data and data.get("items"))


async def _get_counts(db: AsyncDatabaseManager) -> dict:
    async with db._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT status, COUNT(*) AS n FROM articles GROUP BY status"
        )
    counts = {r["status"]: r["n"] for r in rows}
    counts["total"] = sum(counts.values())
    return counts


async def main() -> None:
    db = AsyncDatabaseManager(config.DATABASE_URL, ssl=config.DB_SSL)
    await db.connect()

    t_start = time.monotonic()

    async with VintedAPIClient(
        concurrency=config.CONCURRENCY,
        delay=config.REQUEST_DELAY,
    ) as client:

        cycle = 0
        while True:
            counts = await _get_counts(db)
            elapsed = round(time.monotonic() - t_start)
            print(
                f"\n{'='*60}",
                flush=True,
            )
            print(
                f"[Cycle {cycle}] {elapsed}s elapsed | "
                f"total={counts['total']:,} / {TARGET:,} | "
                f"active={counts.get('active', 0):,} | "
                f"sold={counts.get('sold', 0):,} | "
                f"deleted={counts.get('deleted', 0):,}",
                flush=True,
            )

            if counts["total"] >= TARGET:
                print(f"\n✓ TARGET {TARGET:,} REACHED! Stopping.", flush=True)
                break

            remaining = TARGET - counts["total"]
            print(f"  Need {remaining:,} more articles.", flush=True)

            # ── HEALTH PROBE — skip full scrape if IP is blocked ────────
            print(f"[Cycle {cycle}] → Probing API…", flush=True)
            alive = await _api_is_alive(client)
            if not alive:
                print(
                    f"[Cycle {cycle}] *** BLOCKED — API probe failed. "
                    f"Waiting {BLOCKED_WAIT_S//60}m for Vinted rate-limit to expire… ***",
                    flush=True,
                )
                await asyncio.sleep(BLOCKED_WAIT_S)
                cycle += 1
                continue

            # ── SCRAPE ──────────────────────────────────────────────────
            print(f"[Cycle {cycle}] → Scraping…", flush=True)
            t0 = time.monotonic()
            categories = await fetch_all_categories(client)
            categories = filter_categories(categories, config.EXCLUDE_CATEGORY_PREFIXES)
            stats = await run_full_scrape(client, db, categories)
            scrape_dur = round(time.monotonic() - t0)
            print(
                f"[Cycle {cycle}] Scrape done in {scrape_dur}s — {stats.summary()}",
                flush=True,
            )

            # ── BLOCKED DETECTION (fallback) ─────────────────────────────
            if stats.pages_fetched == 0:
                print(
                    f"[Cycle {cycle}] *** BLOCKED mid-scrape — 0 pages. "
                    f"Waiting {BLOCKED_WAIT_S//60}m… ***",
                    flush=True,
                )
                await asyncio.sleep(BLOCKED_WAIT_S)
                cycle += 1
                continue

            # ── STATUS CHECK ─────────────────────────────────────────────
            print(f"[Cycle {cycle}] → Status check…", flush=True)
            t0 = time.monotonic()
            result = await run_status_check(client, db, limit=500)
            print(
                f"[Cycle {cycle}] Check done in {round(time.monotonic()-t0)}s — "
                f"checked={result['checked']} sold={result['sold']} "
                f"deleted={result['deleted']} errors={result['errors']} "
                f"engagement_updated={result.get('engagement_updated', 0)}",
                flush=True,
            )

            cycle += 1

    await db.close()
    total_elapsed = round(time.monotonic() - t_start)
    print(f"\nTotal time: {total_elapsed//60}m {total_elapsed%60}s", flush=True)


asyncio.run(main())
