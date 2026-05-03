"""
Controlla: Vinted restituisce 404 o 200+is_sold per gli articoli venduti?
Testa item specifici dal log (404 = rimosso, 200 = verifica campi sold).
"""
import asyncio
import re
import sys
import asyncpg
from curl_cffi.requests import AsyncSession

sys.stdout.reconfigure(encoding="utf-8")

DATABASE_URL = "postgresql://postgres.nkjhqqqtfmewifckmyqn:Vinted2026!Secure%23db@aws-1-eu-central-1.pooler.supabase.com:5432/postgres"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"


def extract_csrf(html):
    for pat in (
        r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']',
        r'"csrf_token"\s*:\s*"([^"]+)"',
    ):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None


async def main():
    conn = await asyncpg.connect(DATABASE_URL, ssl="require")

    # Items che il check ha appena marcato come deleted (hanno status=deleted da poco)
    # Confronta con item active ancora
    deleted_new = await conn.fetch(
        "SELECT vinted_id, title FROM articles WHERE status='deleted' ORDER BY id DESC LIMIT 5"
    )
    still_active = await conn.fetch(
        "SELECT vinted_id, title FROM articles WHERE status='active' ORDER BY first_seen_at ASC LIMIT 3"
    )
    await conn.close()

    print(f"Testing {len(deleted_new)} recently-deleted + {len(still_active)} active items")

    async with AsyncSession(impersonate="chrome136") as session:
        hp = await session.get("https://www.vinted.it",
            headers={"Accept": "text/html,*/*", "User-Agent": UA}, timeout=20)
        csrf = extract_csrf(hp.text)
        print(f"CSRF: {'OK' if csrf else 'missing'}")

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.vinted.it/",
            "User-Agent": UA,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        if csrf:
            headers["X-CSRF-Token"] = csrf

        print("\n--- RECENTLY DELETED (should be 404 or 200+sold) ---")
        for row in deleted_new:
            await asyncio.sleep(3.0)
            url = f"https://www.vinted.it/api/v2/items/{row['vinted_id']}"
            resp = await session.get(url, headers=headers, timeout=15)
            print(f"  [{resp.status_code}] {row['vinted_id']} {row['title'][:35]}")
            if resp.status_code == 200:
                item = resp.json().get("item") or resp.json()
                for f in ["is_sold", "is_closed", "item_closing_action", "status", "can_buy"]:
                    if f in item:
                        print(f"       {f}={item[f]!r}")

        print("\n--- STILL ACTIVE (should be 200 + active) ---")
        for row in still_active:
            await asyncio.sleep(3.0)
            url = f"https://www.vinted.it/api/v2/items/{row['vinted_id']}"
            resp = await session.get(url, headers=headers, timeout=15)
            print(f"  [{resp.status_code}] {row['vinted_id']} {row['title'][:35]}")
            if resp.status_code == 200:
                item = resp.json().get("item") or resp.json()
                for f in ["is_sold", "is_closed", "item_closing_action", "status", "can_buy"]:
                    if f in item:
                        print(f"       {f}={item[f]!r}")

asyncio.run(main())
