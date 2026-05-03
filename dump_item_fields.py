"""
Dump ALL fields of one item from a few candidate IDs to understand
what catalog/category metadata the API includes in item objects.
"""
import asyncio, json, sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import config
from src.api_client import VintedAPIClient

SAMPLE_IDS = [9, 11, 1262, 1263, 4, 3]  # mix of known + unknown

async def main():
    async with VintedAPIClient(concurrency=3, delay=0.5) as client:
        for cid in SAMPLE_IDS:
            await asyncio.sleep(0.6)
            data = await client.get_items_page(cid, page=1, per_page=3, retries=1)
            if not data:
                print(f"\n=== ID {cid}: NO_DATA ===")
                continue
            items = data.get("items", [])
            if not items:
                print(f"\n=== ID {cid}: EMPTY ===")
                continue

            item = items[0]
            print(f"\n=== ID {cid} — item '{item.get('title','?')[:40]}' ===")

            # Print all top-level keys and values (truncated)
            for k, v in sorted(item.items()):
                if isinstance(v, (dict, list)):
                    val = json.dumps(v, ensure_ascii=False)[:120]
                else:
                    val = str(v)[:120]
                print(f"  {k}: {val}")

asyncio.run(main())
