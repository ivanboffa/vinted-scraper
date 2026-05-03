"""
Deep-probe candidate category IDs to definitively identify which are adult
clothing categories for donna/uomo on Vinted.it.

For each candidate ID, fetches 10 items and shows:
- All titles (to spot the pattern)
- Item URL (for manual verification if needed)
- Any catalog/category metadata
- Country of seller (to see if ID contains IT items)
"""
import asyncio
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import config
from src.api_client import VintedAPIClient

# ─── Candidates to probe ────────────────────────────────────────────────────
# Low IDs — possible missing main categories
LOW_IDS = [6, 7, 8, 9, 10, 11, 17, 20, 21, 22, 28, 29]

# 1268-1309 — showed tops / shorts in identify_categories; check more carefully
RANGE_1268 = list(range(1268, 1310))

# 1616-1650 — mixed children / women; check which are donna
RANGE_1616 = list(range(1616, 1650))

# 2100-2149 — showed branded sportswear / dresses
RANGE_2100 = list(range(2100, 2150))

# Combine (de-dup, exclude already-known IDs)
KNOWN_IDS = {
    2, 3, 4, 5, 12, 13, 14, 15, 16, 18, 19,
    1187, 1188, 1189, 1190, 1206, 1258, 1261,
    1262, 1263, 1264, 1265, 1266, 1267,
}

ALL_CANDIDATES = sorted(set(LOW_IDS + RANGE_1268 + RANGE_1616 + RANGE_2100) - KNOWN_IDS)

print(f"Probing {len(ALL_CANDIDATES)} candidate IDs...\n", flush=True)


async def probe_id(client, cid: int, sem: asyncio.Semaphore) -> dict:
    async with sem:
        await asyncio.sleep(0.5)
        data = await client.get_items_page(cid, page=1, per_page=10, retries=1)
        if not data:
            return {"id": cid, "status": "NO_DATA", "titles": [], "countries": [], "meta": {}}

        items = data.get("items", [])
        if not items:
            return {"id": cid, "status": "EMPTY", "titles": [], "countries": [], "meta": {}}

        titles = []
        countries = []
        meta_keys = {}

        for item in items:
            title = item.get("title", "?")[:40]
            titles.append(title)

            # Country — user object or user_id location
            user = item.get("user") or {}
            country = (user.get("country_title") or
                       item.get("country") or
                       item.get("user_country") or "?")
            countries.append(country)

            # Any catalog/category metadata
            for key in ("catalog_id", "catalog", "category_id", "category",
                        "catalog_title", "category_title", "size_group_name"):
                if key in item and key not in meta_keys:
                    meta_keys[key] = item[key]

        return {
            "id": cid,
            "status": "OK",
            "titles": titles,
            "countries": countries,
            "meta": meta_keys,
        }


async def main():
    results = []

    async with VintedAPIClient(concurrency=5, delay=0.4) as client:
        sem = asyncio.Semaphore(5)
        tasks = [probe_id(client, cid, sem) for cid in ALL_CANDIDATES]
        raw = await asyncio.gather(*tasks, return_exceptions=True)

    for r in raw:
        if isinstance(r, Exception):
            print(f"  ERROR: {r}", flush=True)
            continue
        results.append(r)

    # Print results grouped
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    for r in sorted(results, key=lambda x: x["id"]):
        cid = r["id"]
        if r["status"] != "OK":
            print(f"  {cid:6d}: {r['status']}")
            continue

        titles_str = " | ".join(r["titles"][:5])
        # Count Italian items
        it_count = sum(1 for c in r["countries"] if c and "ital" in c.lower())
        meta_str = ""
        if r["meta"]:
            meta_str = f"  meta={json.dumps(r['meta'], ensure_ascii=False)[:60]}"
        print(f"  {cid:6d}: IT={it_count}/10  {titles_str[:80]}{meta_str}", flush=True)

    # Save full results
    with open("data/mapped_ids.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\nSaved to data/mapped_ids.json", flush=True)


asyncio.run(main())
