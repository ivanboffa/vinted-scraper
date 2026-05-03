"""Cancella gli articoli salvati con dati rotti (title vuoto o price=0) e resetta il DB."""
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

    before = await conn.fetchval("SELECT COUNT(*) FROM articles")
    print(f"Articoli prima: {before}")

    # Cancella articoli con dati chiaramente rotti (parser vecchio senza fix)
    deleted = await conn.execute("""
        DELETE FROM articles
        WHERE (title IS NULL OR title = '')
           OR (price = 0 AND condition IS NULL)
    """)
    print(f"Cancellati (dati rotti): {deleted}")

    after = await conn.fetchval("SELECT COUNT(*) FROM articles")
    print(f"Articoli dopo: {after}")
    print("Pronto per un nuovo scrape pulito.")

    await conn.close()

asyncio.run(main())
