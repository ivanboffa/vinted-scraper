"""
Investigazione: come trovare articoli venduti su Vinted.
Testa 4 approcci diversi e stampa cosa restituisce l'API.
"""
import asyncio
import os
import sys
from dotenv import load_dotenv
import asyncpg
import ssl as ssl_module

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

from src.api_client import VintedAPIClient, API_BASE, VINTED_BASE


async def test_catalog_sold_filter(client):
    print("\n=== TEST 1: catalog/items?status[]=sold ===")
    for params in [
        {"status[]": "sold", "page": 1, "per_page": 10},
        {"status": "sold",   "page": 1, "per_page": 10},
        {"item_status[]": "sold", "page": 1, "per_page": 10},
    ]:
        await client._rate.acquire()
        try:
            resp = await client._session.get(
                f"{API_BASE}/catalog/items",
                headers=client._api_headers(),
                params={**params, "order": "newest_first"},
                timeout=20,
            )
            items = []
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                sold_in_resp = [i for i in items if i.get("is_sold") or str(i.get("status","")).lower()=="sold"]
                print(f"  {params} -> HTTP {resp.status_code} | items={len(items)} | sold={len(sold_in_resp)}")
            else:
                print(f"  {params} -> HTTP {resp.status_code}")
        except Exception as e:
            print(f"  {params} -> Errore: {e}")


async def test_user_sold_items(client, seller_id):
    print(f"\n=== TEST 2: /users/{seller_id}/items con vari filtri ===")
    for params in [
        {"status": "sold",    "page": 1, "per_page": 20},
        {"status[]": "sold",  "page": 1, "per_page": 20},
        {"page": 1, "per_page": 20},  # senza filtro — vediamo cosa torna
    ]:
        await client._rate.acquire()
        try:
            resp = await client._session.get(
                f"{API_BASE}/users/{seller_id}/items",
                headers=client._api_headers(),
                params=params,
                timeout=20,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                sold = [i for i in items if i.get("is_sold") or str(i.get("status","")).lower()=="sold"]
                statuses = list(set(str(i.get("status","?")) for i in items))
                print(f"  {params} -> HTTP 200 | items={len(items)} | sold={len(sold)} | statuses={statuses[:6]}")
            else:
                print(f"  {params} -> HTTP {resp.status_code}")
        except Exception as e:
            print(f"  {params} -> Errore: {e}")


async def test_item_api(client, vinted_id, label=""):
    print(f"\n=== TEST 3: GET /items/{vinted_id} [{label}] ===")
    status_code, body = await client.get_item(str(vinted_id))
    print(f"  HTTP {status_code}")
    if body:
        item = body.get("item") or body
        print(f"  is_sold={item.get('is_sold')}  is_closed={item.get('is_closed')}")
        print(f"  status={item.get('status')}  item_closing_action={item.get('item_closing_action')}")
        print(f"  can_buy={item.get('can_buy')}")
    else:
        print("  Nessun body (404 o errore)")


async def test_item_web(client, vinted_id, label=""):
    print(f"\n=== TEST 4: web page /items/{vinted_id} [{label}] ===")
    web_status, views, favs = await client.check_item_web(str(vinted_id))
    print(f"  web_status={web_status}  views={views}  favs={favs}")


async def get_db_stats():
    DATABASE_URL = os.getenv("DATABASE_URL", "")
    ctx = ssl_module.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl_module.CERT_NONE
    conn = await asyncpg.connect(DATABASE_URL, ssl=ctx)

    total     = await conn.fetchval("SELECT COUNT(*) FROM articles")
    by_status = await conn.fetch("SELECT status, COUNT(*) n FROM articles GROUP BY status ORDER BY n DESC")
    sold_row  = await conn.fetchrow("SELECT vinted_id, seller_id FROM articles WHERE status='sold'   LIMIT 1")
    del_row   = await conn.fetchrow("SELECT vinted_id, seller_id FROM articles WHERE status='deleted' LIMIT 1")
    active_row= await conn.fetchrow("SELECT vinted_id, seller_id FROM articles WHERE status='active' AND seller_id IS NOT NULL LIMIT 1")

    await conn.close()
    return total, by_status, sold_row, del_row, active_row


async def main():
    print("Recupero stato DB...")
    total, by_status, sold_row, del_row, active_row = await get_db_stats()

    print(f"\n=== STATO DB ({total} articoli totali) ===")
    for r in by_status:
        pct = 100 * r["n"] / total if total else 0
        print(f"  {r['status']:10s}: {r['n']:>6} ({pct:.1f}%)")

    async with VintedAPIClient(concurrency=1, delay=1.5) as client:
        await test_catalog_sold_filter(client)

        seller_id = (sold_row or del_row or active_row or {}).get("seller_id")
        if seller_id:
            await test_user_sold_items(client, seller_id)

        # Test su articolo sold
        if sold_row:
            await test_item_api(client, sold_row["vinted_id"], "SOLD nel DB")
            await test_item_web(client, sold_row["vinted_id"], "SOLD nel DB")
        # Test su articolo deleted
        if del_row:
            await test_item_api(client, del_row["vinted_id"], "DELETED nel DB")
            await test_item_web(client, del_row["vinted_id"], "DELETED nel DB")
        # Fallback su articolo active
        if active_row and not sold_row and not del_row:
            await test_item_api(client, active_row["vinted_id"], "ACTIVE nel DB (fallback)")
            await test_item_web(client, active_row["vinted_id"], "ACTIVE nel DB (fallback)")

    print("\n=== FINE ===")


asyncio.run(main())
