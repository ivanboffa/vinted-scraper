"""
Full growth cycle: scrape all categories + dual status-check pass.

Each cycle:
  1. SCRAPE   — fetch all 27 categories, insert new articles
  2. CHECK-NEW — status-check 3 000 newest active items (might sell fast)
  3. CHECK-OLD — status-check 5 000 oldest active items (most likely gone)

Loops until DB reaches TARGET or SIGINT/SIGTERM.
Progress is printed after every step.
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

CHECK_NEW_LIMIT = 3_000   # newest articles (sell fast)
CHECK_OLD_LIMIT = 5_000   # oldest articles (most likely already gone)


async def _get_counts(db: AsyncDatabaseManager) -> dict:
    async with db._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT status, COUNT(*) AS n FROM articles GROUP BY status"
        )
    counts = {r["status"]: r["n"] for r in rows}
    counts["total"] = sum(counts.values())
    return counts


def _bar(current: int, target: int, width: int = 40) -> str:
    pct = min(current / target, 1.0)
    filled = int(pct * width)
    return f"[{'#' * filled}{'.' * (width - filled)}] {pct*100:.1f}%"


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
                f"\n{'='*65}\n"
                f"[Ciclo {cycle}] {elapsed//60}m{elapsed%60}s trascorsi\n"
                f"  Totale: {counts['total']:>8,} / {TARGET:,}  {_bar(counts['total'], TARGET)}\n"
                f"  Attivi: {counts.get('active', 0):>8,}  |  "
                f"Venduti: {counts.get('sold', 0):,}  |  "
                f"Eliminati: {counts.get('deleted', 0):,}",
                flush=True,
            )

            if counts["total"] >= TARGET:
                print(f"\nTARGET {TARGET:,} RAGGIUNTO! Stop.", flush=True)
                break

            remaining = TARGET - counts["total"]
            print(f"  Mancano ancora {remaining:,} articoli.", flush=True)

            # ── 1. SCRAPE ────────────────────────────────────────────────────
            print(f"\n[Ciclo {cycle}] SCRAPE in corso…", flush=True)
            t0 = time.monotonic()
            categories = await fetch_all_categories(client)
            categories = filter_categories(categories, config.EXCLUDE_CATEGORY_PREFIXES)
            print(f"  Categorie: {len(categories)}", flush=True)

            stats = await run_full_scrape(client, db, categories)
            scrape_dur = round(time.monotonic() - t0)
            print(
                f"[Ciclo {cycle}] Scrape completato in {scrape_dur}s — {stats.summary()}",
                flush=True,
            )

            if stats.pages_fetched == 0:
                wait_s = 900
                print(
                    f"[Ciclo {cycle}] *** BLOCCATO — 0 pagine scaricate. "
                    f"Attesa {wait_s//60}m prima di riprovare… ***",
                    flush=True,
                )
                await asyncio.sleep(wait_s)
                cycle += 1
                continue

            # ── 2. CHECK-NEW ─────────────────────────────────────────────────
            print(f"\n[Ciclo {cycle}] CHECK-NEW ({CHECK_NEW_LIMIT} articoli più recenti)…", flush=True)
            t0 = time.monotonic()
            r_new = await run_status_check(client, db, limit=CHECK_NEW_LIMIT, oldest_first=False)
            print(
                f"[Ciclo {cycle}] CHECK-NEW completato in {round(time.monotonic()-t0)}s — "
                f"checked={r_new['checked']} sold={r_new['sold']} "
                f"deleted={r_new['deleted']} errors={r_new['errors']}",
                flush=True,
            )

            # ── 3. CHECK-OLD ─────────────────────────────────────────────────
            print(f"\n[Ciclo {cycle}] CHECK-OLD ({CHECK_OLD_LIMIT} articoli più vecchi)…", flush=True)
            t0 = time.monotonic()
            r_old = await run_status_check(client, db, limit=CHECK_OLD_LIMIT, oldest_first=True)
            print(
                f"[Ciclo {cycle}] CHECK-OLD completato in {round(time.monotonic()-t0)}s — "
                f"checked={r_old['checked']} sold={r_old['sold']} "
                f"deleted={r_old['deleted']} errors={r_old['errors']}",
                flush=True,
            )

            cycle += 1

    await db.close()
    total_elapsed = round(time.monotonic() - t_start)
    print(f"\nTempo totale: {total_elapsed//60}m {total_elapsed%60}s", flush=True)


asyncio.run(main())
