"""
Fetch one sample item from each discovered ID to identify the category name.
"""
import asyncio, sys, json
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import config
from src.api_client import VintedAPIClient

# All new IDs found by probe (excluding already-known ones)
NEW_IDS = [
    1, 6, 7, 8, 9, 10, 11, 17, 20, 21, 22, 23, 24,
    1180, 1181, 1182, 1183, 1184, 1185, 1186,
    1191, 1192, 1193, 1194, 1195, 1196, 1197, 1198, 1199,
    1200, 1201, 1202, 1203, 1204, 1205,
    1207, 1208, 1209, 1210, 1211, 1212, 1213, 1214,
    1250, 1251, 1252, 1253, 1254, 1255, 1256, 1257,
    1268, 1269, 1270, 1271, 1272, 1273, 1275, 1279,
    1280, 1281, 1282, 1283, 1284, 1285, 1286, 1287, 1288, 1289,
    1290, 1291, 1292, 1293, 1294, 1295, 1296, 1297, 1298, 1299,
    1300, 1305, 1310, 1315, 1320, 1325, 1330, 1335, 1340, 1345,
    1350, 1355, 1360, 1365, 1370, 1374, 1380, 1385, 1390, 1395,
    1400, 1405, 1410, 1418, 1419,
    1600, 1601, 1602, 1604, 1606, 1612, 1616, 1618, 1619,
    1632, 1635, 1638, 1641, 1645, 1649,
    1901, 1902, 1903, 1904, 1905, 1906, 1907, 1908,
    1911, 1915, 1920, 1925, 1930, 1935, 1940, 1945, 1949,
    2100, 2101, 2102, 2103, 2104, 2105, 2106, 2107, 2108,
    2116, 2132, 2135, 2139, 2141, 2143, 2144, 2145, 2146, 2147, 2148, 2149,
]


async def get_sample_title(client, cid: int, semaphore: asyncio.Semaphore) -> tuple[int, str]:
    async with semaphore:
        await asyncio.sleep(0.4)
        data = await client.get_items_page(cid, page=1, per_page=3, retries=1)
        if not data:
            return cid, "NO_DATA"
        items = data.get("items", [])
        if not items:
            return cid, "EMPTY"
        item = items[0]
        title = item.get("title", "?")
        # Try to get catalog info from item
        catalog = item.get("catalog") or item.get("catalog_id") or ""
        catalog_name = ""
        if isinstance(catalog, dict):
            catalog_name = catalog.get("title") or catalog.get("name") or ""
        elif catalog:
            catalog_name = str(catalog)
        # Also look at category_title or path
        cat_title = (item.get("category_title") or item.get("category") or
                     item.get("catalog_title") or "")
        return cid, f"{title[:40]} | cat={catalog_name or cat_title or '?'}"


async def main():
    print(f"Sampling {len(NEW_IDS)} IDs...\n", flush=True)
    results = {}

    async with VintedAPIClient(concurrency=5, delay=0.4) as client:
        semaphore = asyncio.Semaphore(5)
        tasks = [get_sample_title(client, cid, semaphore) for cid in NEW_IDS]
        raw = await asyncio.gather(*tasks, return_exceptions=True)

    for r in raw:
        if isinstance(r, Exception):
            continue
        cid, info = r
        results[cid] = info

    print("\n=== RESULTS ===\n", flush=True)
    for cid in sorted(results):
        print(f"  {cid:6d}: {results[cid]}", flush=True)

    # Save
    with open("data/category_samples.json", "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in results.items()}, f, ensure_ascii=False, indent=2)
    print("\nSaved to data/category_samples.json", flush=True)


asyncio.run(main())
