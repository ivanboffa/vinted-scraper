"""
Extract Vinted category IDs by:
1. Fetching catalog pages and parsing catalog_ids from links
2. Trying the /api/v2/catalogs endpoint with fresh cookies
3. Parsing sidebar/filter data from search result pages
"""
import asyncio, re, json, sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from curl_cffi.requests import AsyncSession

BASE = "https://www.vinted.it"
NEXT_DATA_RE = re.compile(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.DOTALL)
CATALOG_ID_RE = re.compile(r'catalog_ids\[\]=(\d+)')


def find_all_catalog_ids(html: str) -> set[int]:
    """Extract all catalog_ids from page links."""
    return {int(m) for m in CATALOG_ID_RE.findall(html)}


def walk_json_for_catalogs(obj, depth=0) -> list[dict]:
    """Recursively find objects that look like Vinted catalog nodes."""
    if depth > 15:
        return []
    results = []
    if isinstance(obj, dict):
        # A catalog node usually has id + title + (catalogs or children)
        if "id" in obj and ("title" in obj or "name" in obj):
            kids = obj.get("catalogs") or obj.get("children") or obj.get("subcategories") or []
            if isinstance(kids, list):
                results.append(obj)
        for v in obj.values():
            results.extend(walk_json_for_catalogs(v, depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(walk_json_for_catalogs(item, depth + 1))
    return results


async def try_api(session) -> list[dict] | None:
    """Try all known catalog API variants."""
    for url, params in [
        (f"{BASE}/api/v2/catalogs", {"per_page": 500}),
        (f"{BASE}/api/v2/catalogs", {"page": 1}),
        (f"{BASE}/api/v2/catalog", {}),
    ]:
        try:
            r = await session.get(url, headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": f"{BASE}/",
                "X-App-Version": "23.26.0",
            }, params=params, timeout=15)
            print(f"  {url} -> {r.status_code}", flush=True)
            if r.status_code == 200:
                data = r.json()
                cats = data.get("catalogs") or data.get("categories") or []
                if cats:
                    return cats
        except Exception as e:
            print(f"  Error: {e}", flush=True)
    return None


async def main():
    session = AsyncSession(impersonate="chrome136")

    print("=== Step 1: Get cookies from homepage ===", flush=True)
    r = await session.get(BASE, headers={"Accept": "text/html,*/*"}, timeout=20)
    print(f"  Homepage: {r.status_code}", flush=True)

    # Check __NEXT_DATA__ on homepage for category menu
    m = NEXT_DATA_RE.search(r.text)
    if m:
        data = json.loads(m.group(1))
        nodes = walk_json_for_catalogs(data)
        print(f"  Found {len(nodes)} catalog-like nodes in homepage __NEXT_DATA__", flush=True)
        if nodes:
            _process_nodes(nodes)
            await session.close()
            return

    print("\n=== Step 2: Try catalog API endpoints ===", flush=True)
    cats = await try_api(session)
    if cats:
        print(f"  Got {len(cats)} top-level catalogs from API", flush=True)
        _flatten_and_print(cats)
        await session.close()
        return

    print("\n=== Step 3: Parse links from catalog search pages ===", flush=True)
    all_ids = set()

    # Known parent category IDs to explore
    seed_ids = [4, 3, 5, 16, 19, 18, 1187, 1189, 1190, 2,   # donna
                12, 1258, 1261, 1262, 1263, 1264, 13, 14, 15, 1188, 1265, 1266, 1267]  # uomo

    for cid in seed_ids[:6]:  # test a few first
        url = f"{BASE}/catalog?catalog_ids[]={cid}&page=1"
        try:
            r2 = await session.get(url, headers={
                "Accept": "text/html,*/*",
                "Referer": f"{BASE}/catalog",
            }, timeout=15, allow_redirects=True)
            print(f"  catalog_id={cid}: HTTP {r2.status_code}", flush=True)
            if r2.status_code == 200:
                ids = find_all_catalog_ids(r2.text)
                all_ids.update(ids)
                # Check __NEXT_DATA__
                m2 = NEXT_DATA_RE.search(r2.text)
                if m2:
                    data2 = json.loads(m2.group(1))
                    nodes = walk_json_for_catalogs(data2)
                    print(f"    {len(nodes)} catalog nodes in __NEXT_DATA__, {len(ids)} IDs in links", flush=True)
                    if nodes:
                        _process_nodes(nodes)
                        await session.close()
                        return
                else:
                    print(f"    No __NEXT_DATA__. IDs found in links: {sorted(ids)[:20]}", flush=True)
        except Exception as e:
            print(f"  Error for {cid}: {e}", flush=True)

    if all_ids:
        print(f"\nAll IDs found in links: {sorted(all_ids)}", flush=True)

    await session.close()


def _process_nodes(nodes: list[dict]):
    seen = {}
    for node in nodes:
        cid = node.get("id")
        title = node.get("title") or node.get("name") or "?"
        if cid and cid not in seen:
            seen[cid] = title
    print(f"\nUnique catalog nodes: {len(seen)}", flush=True)
    for cid, title in sorted(seen.items()):
        print(f"  ({cid}, \"{title}\")", flush=True)


def _flatten_and_print(cats: list, parent=""):
    for cat in cats:
        cid = cat.get("id")
        title = cat.get("title") or cat.get("name") or "?"
        path = f"{parent}/{title}".strip("/") if parent else title
        if cid:
            print(f"  ({cid:6d}, \"{path}\"),", flush=True)
        children = cat.get("catalogs") or cat.get("children") or []
        if children:
            _flatten_and_print(children, path)


asyncio.run(main())
