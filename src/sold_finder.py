"""
Sold Finder — due strategie per trovare articoli venduti.

Strategia 1 — Reclassify:
    Ri-controlla tutti gli articoli con status='deleted' via web page.
    Quelli che risultano 'sold' vengono corretti nel DB.
    Motivazione: prima di check_item_web, ogni 404 veniva segnato come
    deleted anche se l'articolo era sold.

Strategia 2 — Seller scan:
    Per ogni seller_id unico nel DB, controlla i loro annunci e cerca
    articoli sold che non abbiamo ancora nel DB.
    Motivazione: i venditori che abbiamo già incontrato probabilmente
    hanno venduto altri articoli che non abbiamo mai scrapato.

Entrambe le strategie aggiornano direttamente il DB.
"""

import asyncio
import logging
import random
import time
from datetime import datetime, timezone

import asyncpg

from .api_client import VintedAPIClient
from .db_manager import AsyncDatabaseManager

logger = logging.getLogger(__name__)

_CONCURRENCY = 3
_DELAY_BASE  = 2.2
_DELAY_JITTER = 0.5


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Strategia 1: Reclassify deleted → sold ────────────────────────────────

async def _reclassify_one(
    vinted_id: str,
    client: VintedAPIClient,
    db: AsyncDatabaseManager,
    semaphore: asyncio.Semaphore,
    stats: dict,
) -> None:
    async with semaphore:
        await asyncio.sleep(_DELAY_BASE + random.uniform(0, _DELAY_JITTER))
        web_status, views, favourites = await client.check_item_web(vinted_id)

    stats["checked"] += 1

    if web_status == "sold":
        await db.mark_as_sold(vinted_id)
        stats["reclassified_sold"] += 1
        logger.info("Reclassified SOLD: %s", vinted_id)
    elif web_status == "deleted":
        stats["confirmed_deleted"] += 1
    elif web_status == "active":
        # Item is back active (seller re-listed or our status was wrong)
        await db.reactivate(vinted_id)
        stats["reactivated"] += 1
        logger.info("Reactivated: %s", vinted_id)
    else:
        stats["unknown"] += 1  # rate-limited, skip

    if views is not None or favourites is not None:
        await db.update_engagement(vinted_id, views, favourites)


async def reclassify_deleted(
    client: VintedAPIClient,
    db: AsyncDatabaseManager,
    limit: int = 5000,
) -> dict:
    """
    Controlla tutti gli articoli status='deleted' via web e reclassifica
    quelli che risultano sold.

    Returns: {checked, reclassified_sold, confirmed_deleted, reactivated, unknown, duration_seconds}
    """
    t0 = time.monotonic()
    stats = {
        "checked": 0, "reclassified_sold": 0,
        "confirmed_deleted": 0, "reactivated": 0, "unknown": 0,
    }

    if db._pool is None:
        return stats

    async with db._pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT vinted_id FROM articles
            WHERE  status = 'deleted'
            ORDER  BY last_seen_at DESC
            LIMIT  $1
            """,
            limit,
        )

    if not rows:
        logger.info("Reclassify: nessun articolo deleted trovato.")
        stats["duration_seconds"] = 0
        return stats

    logger.info("Reclassify: %d articoli 'deleted' da ri-controllare.", len(rows))
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    tasks = [
        asyncio.create_task(
            _reclassify_one(row["vinted_id"], client, db, semaphore, stats)
        )
        for row in rows
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    stats["duration_seconds"] = round(time.monotonic() - t0, 1)
    logger.info(
        "Reclassify done in %.0fs — checked=%d sold=%d deleted=%d reactivated=%d unknown=%d",
        stats["duration_seconds"],
        stats["checked"], stats["reclassified_sold"],
        stats["confirmed_deleted"], stats["reactivated"], stats["unknown"],
    )
    return stats


# ── Strategia 2: Seller scan ──────────────────────────────────────────────

async def _fetch_seller_items(
    client: VintedAPIClient,
    seller_id: str,
    page: int = 1,
    per_page: int = 96,
) -> list[dict]:
    """
    Chiama /api/v2/users/{seller_id}/items e torna la lista di items.
    Nota: l'API non ha un filtro sold affidabile — torna tutti gli item
    del venditore (active + sold). Filtriamo lato client.
    """
    from .api_client import API_BASE
    await client._rate.acquire()
    try:
        resp = await client._session.get(
            f"{API_BASE}/users/{seller_id}/items",
            headers=client._api_headers(),
            params={"page": page, "per_page": per_page},
            timeout=20,
        )
        if resp.status_code == 200:
            return resp.json().get("items", [])
        if resp.status_code in (403, 429, 404):
            return []
        logger.debug("seller %s items HTTP %s", seller_id, resp.status_code)
        return []
    except Exception as exc:
        logger.debug("seller %s error: %s", seller_id, exc)
        return []


async def _process_seller(
    seller_id: str,
    client: VintedAPIClient,
    db: AsyncDatabaseManager,
    semaphore: asyncio.Semaphore,
    stats: dict,
    existing_ids: set,
) -> None:
    """
    Scarica gli item di un venditore, trova quelli sold non nel DB, li inserisce.
    """
    async with semaphore:
        await asyncio.sleep(_DELAY_BASE + random.uniform(0, _DELAY_JITTER))
        items = await _fetch_seller_items(client, seller_id, page=1, per_page=96)

    stats["sellers_checked"] += 1

    new_sold = []
    for raw in items:
        vid = str(raw.get("id", ""))
        if not vid or vid in existing_ids:
            continue

        is_sold = raw.get("is_sold", False)
        status_raw = str(raw.get("status") or "").lower()
        is_closed = raw.get("is_closed", False)
        closing_action = str(raw.get("item_closing_action") or "").lower()

        if is_sold or status_raw == "sold" or (is_closed and closing_action == "sold"):
            new_sold.append(raw)

    if new_sold:
        await _insert_sold_items(db, new_sold, stats)


async def _insert_sold_items(
    db: AsyncDatabaseManager,
    items: list[dict],
    stats: dict,
) -> None:
    """Inserisce articoli sold scoperti via seller scan direttamente come 'sold'."""
    if not items or db._pool is None:
        return

    rows = []
    now = _now_naive()
    for raw in items:
        vid = str(raw.get("id", ""))
        if not vid:
            continue

        price_field = raw.get("price")
        if isinstance(price_field, dict):
            price_raw = price_field.get("amount") or 0
            currency = price_field.get("currency_code") or "EUR"
        else:
            price_raw = price_field or 0
            currency = raw.get("currency") or "EUR"

        try:
            price = float(str(price_raw).replace(",", ".")) if price_raw else None
        except (ValueError, TypeError):
            price = None

        photos = raw.get("photos") or raw.get("photo")
        if isinstance(photos, list):
            image_url = (photos[0].get("full_size_url") or photos[0].get("url")) if photos else None
        elif isinstance(photos, dict):
            image_url = photos.get("full_size_url") or photos.get("url")
        else:
            image_url = None

        user = raw.get("user") or {}
        seller_id = str(user.get("id") or raw.get("user_id") or "")

        rows.append((
            vid,
            raw.get("title"),
            price,
            currency,
            raw.get("url") or f"https://www.vinted.it/items/{vid}",
            image_url,
            raw.get("brand_title") or raw.get("brand") or "",
            raw.get("size_title") or raw.get("size") or "",
            raw.get("status") or "",
            "",   # category — non disponibile da seller scan
            seller_id or None,
        ))

    try:
        async with db._pool.acquire() as conn:
            inserted = await conn.executemany(
                """
                INSERT INTO articles (
                    vinted_id, title, price, currency, url, image_url,
                    brand, size, condition, category, seller_id,
                    status, sold_at, first_seen_at, last_seen_at
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,
                        'sold', NOW(), NOW(), NOW())
                ON CONFLICT (vinted_id) DO UPDATE SET
                    status   = 'sold',
                    sold_at  = COALESCE(articles.sold_at, NOW()),
                    last_seen_at = NOW()
                WHERE articles.status != 'sold'
                """,
                rows,
            )
        stats["new_sold_inserted"] += len(rows)
        logger.info("Seller scan: inseriti %d sold", len(rows))
    except Exception as exc:
        logger.error("Seller scan insert error: %s", exc)


async def seller_scan(
    client: VintedAPIClient,
    db: AsyncDatabaseManager,
    max_sellers: int = 500,
) -> dict:
    """
    Scansiona i profili dei venditori già noti nel DB cercando articoli
    sold che non abbiamo ancora.

    Returns: {sellers_checked, new_sold_inserted, duration_seconds}
    """
    t0 = time.monotonic()
    stats = {"sellers_checked": 0, "new_sold_inserted": 0}

    if db._pool is None:
        return stats

    async with db._pool.acquire() as conn:
        # Seller unici con più articoli nel DB (più probabile abbiano sold)
        sellers = await conn.fetch(
            """
            SELECT seller_id, COUNT(*) AS n
            FROM   articles
            WHERE  seller_id IS NOT NULL
            GROUP  BY seller_id
            ORDER  BY n DESC
            LIMIT  $1
            """,
            max_sellers,
        )
        # vinted_id già nel DB (per evitare duplicati)
        existing = await conn.fetch("SELECT vinted_id FROM articles")

    existing_ids = {r["vinted_id"] for r in existing}

    if not sellers:
        logger.info("Seller scan: nessun seller_id nel DB ancora.")
        stats["duration_seconds"] = 0
        return stats

    logger.info("Seller scan: %d venditori da scansionare.", len(sellers))
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    tasks = [
        asyncio.create_task(
            _process_seller(
                row["seller_id"], client, db,
                semaphore, stats, existing_ids,
            )
        )
        for row in sellers
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    stats["duration_seconds"] = round(time.monotonic() - t0, 1)
    logger.info(
        "Seller scan done in %.0fs — sellers=%d new_sold=%d",
        stats["duration_seconds"],
        stats["sellers_checked"], stats["new_sold_inserted"],
    )
    return stats


# ── Entry point combinato ─────────────────────────────────────────────────

async def run_sold_finder(
    client: VintedAPIClient,
    db: AsyncDatabaseManager,
    reclassify: bool = True,
    scan_sellers: bool = True,
    max_sellers: int = 500,
    reclassify_limit: int = 5000,
) -> dict:
    """
    Esegue entrambe le strategie in sequenza e ritorna i risultati combinati.
    """
    results = {}

    if reclassify:
        logger.info("=== SOLD FINDER: RECLASSIFY START ===")
        r = await reclassify_deleted(client, db, limit=reclassify_limit)
        results["reclassify"] = r
        logger.info("=== SOLD FINDER: RECLASSIFY DONE ===")

    if scan_sellers:
        logger.info("=== SOLD FINDER: SELLER SCAN START ===")
        s = await seller_scan(client, db, max_sellers=max_sellers)
        results["seller_scan"] = s
        logger.info("=== SOLD FINDER: SELLER SCAN DONE ===")

    return results
