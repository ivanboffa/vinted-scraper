"""
Re-check all items currently marked 'deleted' via the web page
to reclassify them as 'sold' or leave them as 'deleted'.
Run once after deploying the web-check fix.
"""
import asyncio, asyncpg, random, sys, time
sys.stdout.reconfigure(encoding="utf-8")

import config
from src.api_client import VintedAPIClient
from src.db_manager import AsyncDatabaseManager

DATABASE_URL = config.DATABASE_URL

async def main():
    db = AsyncDatabaseManager(DATABASE_URL, ssl=config.DB_SSL)
    await db.connect()

    conn = await asyncpg.connect(DATABASE_URL, ssl="require")
    rows = await conn.fetch(
        "SELECT vinted_id, title FROM articles WHERE status='deleted' ORDER BY id DESC"
    )
    await conn.close()

    total = len(rows)
    print(f"Items to re-check: {total}", flush=True)

    sold_count = 0
    deleted_count = 0
    unknown_count = 0
    t0 = time.monotonic()

    async with VintedAPIClient(concurrency=2, delay=0.5) as client:
        semaphore = asyncio.Semaphore(2)

        async def check_one(row, idx):
            nonlocal sold_count, deleted_count, unknown_count
            async with semaphore:
                await asyncio.sleep(3.0 + random.uniform(0, 1.5))
                web_status, _, _ = await client.check_item_web(row["vinted_id"])

            if web_status == "sold":
                await db.mark_as_sold(row["vinted_id"])
                sold_count += 1
            elif web_status == "deleted":
                deleted_count += 1  # already correct
            else:
                unknown_count += 1  # leave as deleted

            if (idx + 1) % 50 == 0:
                elapsed = time.monotonic() - t0
                pct = (idx + 1) / total * 100
                print(
                    f"  [{idx+1}/{total} {pct:.0f}%] "
                    f"sold={sold_count} deleted={deleted_count} unknown={unknown_count} "
                    f"({elapsed:.0f}s)",
                    flush=True,
                )

        tasks = [asyncio.create_task(check_one(row, i)) for i, row in enumerate(rows)]
        await asyncio.gather(*tasks, return_exceptions=True)

    elapsed = time.monotonic() - t0
    print(f"\nDone in {elapsed:.0f}s", flush=True)
    print(f"  Reclassified as SOLD:    {sold_count}", flush=True)
    print(f"  Confirmed DELETED:       {deleted_count}", flush=True)
    print(f"  Unknown (left deleted):  {unknown_count}", flush=True)

    await db.close()

asyncio.run(main())
