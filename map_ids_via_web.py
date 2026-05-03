"""
For each candidate ID:
  1. Fetch one item from the catalog API
  2. Fetch that item's web page
  3. Extract catalog path from __NEXT_DATA__ (breadcrumb / catalog object)

This definitively maps numeric IDs to human-readable category names.
"""
import asyncio
import json
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from curl_cffi.requests import AsyncSession

import config
from src.api_client import VintedAPIClient

VINTED_BASE = "https://www.vinted.it"

# ─── IDs to investigate ─────────────────────────────────────────────────────
# Low IDs that look like missing women's/men's clothing
LOW = [6, 7, 8, 9, 10, 11, 17, 20, 21, 22, 28, 29]
# Around 1268–1309 (showed tops / shorts)
R1268 = list(range(1268, 1310))
# Around 1616-1650 (mixed children / women)
R1616 = list(range(1616, 1650))
# 2100–2149 (branded clothing)
R2100 = list(range(2100, 2150))

KNOWN_IDS = {
    2, 3, 4, 5, 12, 13, 14, 15, 16, 18, 19,
    1187, 1188, 1189, 1190, 1206, 1258, 1261,
    1262, 1263, 1264, 1265, 1266, 1267,
}

ALL = sorted(set(LOW + R1268 + R1616 + R2100) - KNOWN_IDS)

_NEXT_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.DOTALL
)


def _extract_catalog_path(html: str) -> str:
    """Try to find a category/catalog path in the item page __NEXT_DATA__."""
    m = _NEXT_RE.search(html)
    if not m:
        return "NO_NEXT_DATA"
    try:
        data = json.loads(m.group(1))
        page = data.get("props", {}).get("pageProps", {})
        item = page.get("item") or page.get("itemDto") or {}

        # 1. Direct catalog path array: [{id, title}, ...]
        for key in ("catalog", "category", "breadcrumbs", "catalogPath"):
            val = item.get(key)
            if isinstance(val, list) and val:
                return " > ".join(
                    v.get("title") or v.get("name") or str(v.get("id", "?"))
                    for v in val
                )
            if isinstance(val, dict):
                path_parts = []
                node = val
                while node:
                    name = node.get("title") or node.get("name") or str(node.get("id", "?"))
                    path_parts.append(name)
                    node = node.get("parent") or node.get("parentCatalog")
                return " > ".join(reversed(path_parts))

        # 2. catalog_title / category_title  (flat string)
        for key in ("catalog_title", "category_title", "categoryTitle",
                    "catalogTitle", "catalog_name"):
            val = item.get(key)
            if val:
                return str(val)

        # 3. Look at catalog_id
        cid = item.get("catalog_id") or item.get("catalogId")
        if cid:
            return f"catalog_id={cid}"

        return f"ITEM_KEYS={list(item.keys())[:20]}"
    except Exception as exc:
        return f"PARSE_ERR: {exc}"


async def get_item_url(client: VintedAPIClient, cid: int) -> str | None:
    data = await client.get_items_page(cid, page=1, per_page=3, retries=1)
    if not data:
        return None
    items = data.get("items", [])
    if not items:
        return None
    item = items[0]
    item_id = item.get("id")
    if not item_id:
        return None
    return f"{VINTED_BASE}/items/{item_id}"


async def main():
    session = AsyncSession(impersonate="chrome136")

    # Warm cookies
    await session.get(VINTED_BASE, headers={"Accept": "text/html,*/*"}, timeout=20)

    results = {}

    async with VintedAPIClient(concurrency=5, delay=0.5) as client:
        # Phase 1: get one item URL per candidate ID
        print(f"Phase 1: fetching item URLs for {len(ALL)} IDs...", flush=True)
        urls: dict[int, str | None] = {}
        sem = asyncio.Semaphore(5)

        async def _get_url(cid):
            async with sem:
                await asyncio.sleep(0.4)
                url = await get_item_url(client, cid)
                urls[cid] = url

        await asyncio.gather(*[_get_url(cid) for cid in ALL])

    # Phase 2: fetch each item web page and extract catalog path
    print(f"\nPhase 2: fetching {sum(1 for u in urls.values() if u)} item pages...", flush=True)
    sem2 = asyncio.Semaphore(4)

    async def _fetch_page(cid, url):
        async with sem2:
            await asyncio.sleep(0.6)
            if not url:
                results[cid] = "NO_ITEM"
                return
            try:
                r = await session.get(
                    url,
                    headers={
                        "Accept": "text/html,*/*",
                        "Referer": VINTED_BASE + "/",
                    },
                    timeout=20,
                    allow_redirects=True,
                )
                if r.status_code == 200:
                    results[cid] = _extract_catalog_path(r.text)
                else:
                    results[cid] = f"HTTP_{r.status_code}"
            except Exception as e:
                results[cid] = f"ERR: {e}"

    await asyncio.gather(*[_fetch_page(cid, urls[cid]) for cid in ALL])
    await session.close()

    print("\n" + "=" * 70)
    print("CATALOG PATH PER ID")
    print("=" * 70)
    for cid in sorted(results):
        url = urls.get(cid, "?")
        path = results[cid]
        print(f"  {cid:6d}: {path}  [{url}]", flush=True)

    with open("data/id_catalog_paths.json", "w", encoding="utf-8") as f:
        json.dump(
            {str(cid): {"path": results.get(cid, "?"), "url": urls.get(cid)}
             for cid in sorted(ALL)},
            f, ensure_ascii=False, indent=2,
        )
    print("\nSaved to data/id_catalog_paths.json", flush=True)


asyncio.run(main())
