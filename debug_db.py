"""Verifica i dati salvati nel DB — mostra i primi 3 articoli con tutti i campi."""
import asyncio, os, ssl as ssl_module
from dotenv import load_dotenv
import asyncpg

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "")

async def main():
    ctx = ssl_module.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl_module.CERT_NONE
    conn = await asyncpg.connect(DATABASE_URL, ssl=ctx)

    # Conta totale
    total = await conn.fetchval("SELECT COUNT(*) FROM articles")
    print(f"Totale articoli nel DB: {total}\n")

    # Controlla quanti hanno i campi NULL
    nulls = await conn.fetchrow("""
        SELECT
            COUNT(*) FILTER (WHERE title IS NULL)          AS null_title,
            COUNT(*) FILTER (WHERE price IS NULL)          AS null_price,
            COUNT(*) FILTER (WHERE brand IS NULL OR brand = '') AS null_brand,
            COUNT(*) FILTER (WHERE size IS NULL OR size = '')  AS null_size,
            COUNT(*) FILTER (WHERE condition IS NULL OR condition = '') AS null_condition,
            COUNT(*) FILTER (WHERE photo_count IS NULL)    AS null_photo_count,
            COUNT(*) FILTER (WHERE views_count IS NULL)    AS null_views_count,
            COUNT(*) FILTER (WHERE favourite_count IS NULL) AS null_fav
        FROM articles
    """)
    print("=== CAMPI NULL / VUOTI ===")
    for k, v in nulls.items():
        pct = round(v / total * 100, 1) if total else 0
        print(f"  {k}: {v} ({pct}%)")

    # Mostra 3 articoli campione
    rows = await conn.fetch("""
        SELECT vinted_id, title, price, brand, size, condition,
               photo_count, views_count, favourite_count, status
        FROM articles LIMIT 3
    """)
    print("\n=== CAMPIONE 3 ARTICOLI ===")
    for r in rows:
        for k, v in r.items():
            print(f"  {k}: {repr(v)}")
        print()

    await conn.close()

asyncio.run(main())
