"""
Discover all Vinted.it clothing category IDs by fetching the catalog page
and extracting the full category tree from __NEXT_DATA__ JSON.
"""
import asyncio
import json
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

from curl_cffi.requests import AsyncSession

VINTED_BASE = "https://www.vinted.it"


def _extract_next_data(html: str) -> dict:
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html, re.DOTALL,
    )
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return {}


def _walk_catalogs(catalogs: list, parent: str = "") -> list[tuple[int, str]]:
    """Recursively walk catalog tree, return list of (id, path) for every node."""
    results = []
    for cat in catalogs:
        title = cat.get("title") or cat.get("name") or str(cat.get("id", "?"))
        path = f"{parent}/{title}".strip("/") if parent else title
        cid = cat.get("id")
        if cid:
            results.append((cid, path))
        children = cat.get("catalogs") or cat.get("children") or []
        if children:
            results.extend(_walk_catalogs(children, path))
    return results


async def main():
    session = AsyncSession(impersonate="chrome136")

    # 1. Get cookies from homepage
    print("Fetching homepage for cookies...", flush=True)
    await session.get(VINTED_BASE, timeout=20)

    # 2. Try the API endpoint directly
    print("Trying /api/v2/catalogs...", flush=True)
    resp = await session.get(
        f"{VINTED_BASE}/api/v2/catalogs",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{VINTED_BASE}/",
        },
        params={"per_page": 500},
        timeout=20,
    )
    print(f"  Status: {resp.status_code}", flush=True)

    if resp.status_code == 200:
        data = resp.json()
        catalogs = data.get("catalogs", [])
        if catalogs:
            all_cats = _walk_catalogs(catalogs)
            print(f"\nFound {len(all_cats)} total categories from API.", flush=True)
            _print_and_save(all_cats)
            await session.close()
            return

    # 3. Try fetching individual category pages to extract __NEXT_DATA__
    print("API failed, trying catalog pages...", flush=True)

    all_cats = []
    # Try fetching the women's and men's main catalog pages
    for url in [
        f"{VINTED_BASE}/catalog",
        f"{VINTED_BASE}/femmes",
        f"{VINTED_BASE}/hommes",
        f"{VINTED_BASE}/donna",
        f"{VINTED_BASE}/uomo",
        f"{VINTED_BASE}/catalog?catalog_ids[]=4",   # donna/vestiti
    ]:
        try:
            r = await session.get(
                url,
                headers={
                    "Accept": "text/html,application/xhtml+xml,*/*",
                    "Referer": f"{VINTED_BASE}/",
                },
                timeout=20,
                allow_redirects=True,
            )
            print(f"  {url} -> {r.status_code}", flush=True)
            if r.status_code == 200:
                data = _extract_next_data(r.text)
                # Look for catalog tree anywhere in the page data
                cats = _find_catalogs_in_data(data)
                if cats:
                    print(f"    Found {len(cats)} categories in __NEXT_DATA__", flush=True)
                    all_cats.extend(cats)
                    break
        except Exception as e:
            print(f"  Error {url}: {e}", flush=True)

    if all_cats:
        _print_and_save(all_cats)
    else:
        # 4. Last resort: try getting catalog data from items endpoint response
        print("\nTrying items API response for category info...", flush=True)
        r = await session.get(
            f"{VINTED_BASE}/api/v2/catalog/items",
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": f"{VINTED_BASE}/",
            },
            params={
                "catalog_ids[]": 4,
                "page": 1,
                "per_page": 1,
                "order": "newest_first",
            },
            timeout=20,
        )
        print(f"  Items API status: {r.status_code}", flush=True)
        if r.status_code == 200:
            data = r.json()
            # Look for catalogs key in response
            cats_data = data.get("catalogs") or data.get("dominant_catalog") or {}
            print("  Response keys:", list(data.keys()), flush=True)
            if cats_data:
                print(json.dumps(cats_data, indent=2, ensure_ascii=False)[:2000], flush=True)

    await session.close()


def _find_catalogs_in_data(data: dict, depth: int = 0) -> list[tuple[int, str]]:
    """Recursively search for 'catalogs' arrays in any nested dict."""
    if depth > 10:
        return []
    results = []
    if isinstance(data, dict):
        for key, val in data.items():
            if key in ("catalogs", "categories", "allCatalogs") and isinstance(val, list):
                found = _walk_catalogs(val)
                if found:
                    results.extend(found)
            elif isinstance(val, (dict, list)):
                results.extend(_find_catalogs_in_data(val, depth + 1))
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                results.extend(_find_catalogs_in_data(item, depth + 1))
    return results


def _print_and_save(cats: list[tuple[int, str]]):
    # Deduplicate
    seen = set()
    unique = []
    for cid, path in cats:
        if cid not in seen:
            seen.add(cid)
            unique.append((cid, path))

    unique.sort(key=lambda x: x[1])

    print(f"\n{'='*60}", flush=True)
    print(f"TOTAL UNIQUE CATEGORIES: {len(unique)}", flush=True)
    print(f"{'='*60}", flush=True)
    for cid, path in unique:
        print(f"  ({cid:6d}, \"{path}\"),", flush=True)

    # Save to file for reference
    with open("data/discovered_categories.json", "w", encoding="utf-8") as f:
        json.dump([{"id": cid, "path": path} for cid, path in unique], f,
                  ensure_ascii=False, indent=2)
    print(f"\nSaved to data/discovered_categories.json", flush=True)


asyncio.run(main())
