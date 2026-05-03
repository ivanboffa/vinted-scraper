import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).parent.parent / "data" / "categories_cache.json"
CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 days

# Verified leaf category IDs for vinted.it — donna + uomo only.
# Tested against the live catalog; IDs that return 0 results are excluded.
FALLBACK_CATEGORIES: list[tuple[int, str]] = [
    # ── DONNA ────────────────────────────────────────────────
    (2,    "donna/abbigliamento"),        # parent — broad women's clothing
    (4,    "donna/vestiti"),              # dresses
    (3,    "donna/top-maglie"),           # tops & knitwear
    (5,    "donna/giacche-cappotti"),     # jackets & coats
    (8,    "donna/tailleur-blazer"),      # suits, blazers, two-pieces
    (9,    "donna/pantaloni"),            # trousers (confirmed leaf category)
    (11,   "donna/gonne"),               # skirts (confirmed leaf category)
    (1206, "donna/jeans"),
    (16,   "donna/scarpe"),
    (19,   "donna/borse"),
    (18,   "donna/accessori"),
    (1187, "donna/abbigliamento-sportivo"),
    (1189, "donna/costumi-da-bagno"),
    (1190, "donna/intimo-pigiami"),

    # ── UOMO ─────────────────────────────────────────────────
    (12,   "uomo/abbigliamento"),         # parent — broad men's clothing
    (1258, "uomo/t-shirt-polo"),
    # 1259 (camicie) and 1260 (maglioni-felpe) confirmed invalid — API returns NO_ITEMS
    (1261, "uomo/giacche-cappotti"),
    (1262, "uomo/pantaloni"),
    (1263, "uomo/jeans"),
    (1264, "uomo/shorts"),
    (13,   "uomo/scarpe"),
    (14,   "uomo/borse-zaini"),
    (15,   "uomo/accessori"),
    (1188, "uomo/abbigliamento-sportivo"),
    (1265, "uomo/intimo-pigiami"),
    (1266, "uomo/costumi-da-bagno"),
    (1267, "uomo/abbigliamento-formale"),
]


def _flatten_categories(catalogs: list[dict], parent_path: str = "") -> list[tuple[int, str]]:
    result = []
    for cat in catalogs:
        name = cat.get("title") or cat.get("name") or str(cat.get("id", ""))
        path = f"{parent_path}/{name}".strip("/") if parent_path else name
        children = cat.get("catalogs", [])
        if children:
            result.extend(_flatten_categories(children, path))
        else:
            result.append((cat["id"], path))
    return result


def _cache_is_fresh() -> bool:
    if not CACHE_PATH.exists():
        return False
    age = time.time() - CACHE_PATH.stat().st_mtime
    return age < CACHE_TTL_SECONDS


def load_from_cache() -> list[tuple[int, str]] | None:
    if not _cache_is_fresh():
        if CACHE_PATH.exists():
            logger.info("Category cache expired — will refresh from API")
        return None
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        cats = [(item["id"], item["path"]) for item in data]
        logger.info("Loaded %d categories from cache", len(cats))
        return cats
    except Exception as exc:
        logger.warning("Cache read failed: %s", exc)
        return None


def save_to_cache(categories: list[tuple[int, str]]):
    try:
        os.makedirs(CACHE_PATH.parent, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps(
                [{"id": cid, "path": path} for cid, path in categories],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("Cached %d categories to %s", len(categories), CACHE_PATH)
    except Exception as exc:
        logger.warning("Cache write failed: %s", exc)


def filter_categories(
    categories: list[tuple[int, str]],
    exclude_prefixes: list[str],
) -> list[tuple[int, str]]:
    """Remove any category whose path starts with one of the excluded prefixes."""
    if not exclude_prefixes:
        return categories
    lowered = [p.lower() for p in exclude_prefixes]
    filtered = [
        (cid, path)
        for cid, path in categories
        if not any(path.lower().startswith(p) for p in lowered)
    ]
    removed = len(categories) - len(filtered)
    if removed:
        logger.info("Filtered out %d categories (excluded: %s)", removed, exclude_prefixes)
    return filtered


async def fetch_all_categories(client) -> list[tuple[int, str]]:
    """
    Fetch the full category tree from Vinted API.
    Uses a 7-day local cache. Falls back to FALLBACK_CATEGORIES on error.
    """
    cached = load_from_cache()
    if cached:
        return cached

    raw = await client.get_categories()
    if raw:
        categories = _flatten_categories(raw)
        if categories:
            save_to_cache(categories)
            logger.info("Fetched %d leaf categories from Vinted API", len(categories))
            return categories
        logger.warning("API category list was empty after flattening")
    else:
        logger.warning("Category API returned nothing — using fallback list")

    return FALLBACK_CATEGORIES
