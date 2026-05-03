"""
Test check_item_web on a few items that were marked as deleted (API 404)
to see if the website distinguishes sold vs deleted.
"""
import asyncio, asyncpg, sys
sys.stdout.reconfigure(encoding="utf-8")

import config
from src.api_client import VintedAPIClient

DATABASE_URL = "postgresql://postgres.nkjhqqqtfmewifckmyqn:Vinted2026!Secure%23db@aws-1-eu-central-1.pooler.supabase.com:5432/postgres"

async def main():
    conn = await asyncpg.connect(DATABASE_URL, ssl="require")
    # Most recently deleted items (marked by the last status check)
    rows = await conn.fetch(
        "SELECT vinted_id, title FROM articles WHERE status='deleted' ORDER BY id DESC LIMIT 10"
    )
    await conn.close()

    async with VintedAPIClient(concurrency=2, delay=1.0) as client:
        for row in rows:
            vid = row["vinted_id"]
            status, views, favourites = await client.check_item_web(vid)
            print(f"  [{status:>10}] views={views} fav={favourites}  {vid}  {row['title'][:45]}", flush=True)
            await asyncio.sleep(2.0)

asyncio.run(main())
