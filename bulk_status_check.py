"""
Run many status check cycles to maximise sold detection.
Checks 2000 items per cycle, up to MAX_CYCLES cycles.
"""
import asyncio, sys, time
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

import config
from src.api_client import VintedAPIClient
from src.db_manager import AsyncDatabaseManager
from src.status_checker import run_status_check

MAX_CYCLES = 50     # check up to 100k items total
BATCH      = 2000   # items per cycle

async def main():
    db = AsyncDatabaseManager(config.DATABASE_URL, ssl=config.DB_SSL)
    await db.connect()
    t0 = time.monotonic()
    async with VintedAPIClient(concurrency=config.CONCURRENCY, delay=config.REQUEST_DELAY) as client:
        for i in range(MAX_CYCLES):
            t1 = time.monotonic()
            res = await run_status_check(client, db, limit=BATCH)
            elapsed = round(time.monotonic()-t1)
            print(f"[{i+1:02d}/{MAX_CYCLES}] {elapsed:4}s  "
                  f"checked={res['checked']}  sold={res['sold']}  "
                  f"deleted={res['deleted']}  errors={res['errors']}")
            if res['checked'] == 0:
                print("Nothing left to check — done.")
                break
    await db.close()
    total = round(time.monotonic()-t0)
    print(f"\nDone in {total//60}m {total%60}s")

asyncio.run(main())
