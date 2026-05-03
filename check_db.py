import asyncio
import asyncpg

DATABASE_URL = "postgresql://postgres.nkjhqqqtfmewifckmyqn:Vinted2026!Secure%23db@aws-1-eu-central-1.pooler.supabase.com:5432/postgres"

async def check():
    conn = await asyncpg.connect(DATABASE_URL, ssl="require")

    total = await conn.fetchval("SELECT COUNT(*) FROM articles")
    print(f"Totale articoli: {total}")

    statuses = await conn.fetch("SELECT status, COUNT(*) as cnt FROM articles GROUP BY status ORDER BY cnt DESC")
    print("Per status:")
    for r in statuses:
        print(f"  {r['status']}: {r['cnt']}")

    by_cat = await conn.fetch("SELECT category, COUNT(*) as cnt FROM articles GROUP BY category ORDER BY cnt DESC LIMIT 10")
    print("Top 10 categorie:")
    for r in by_cat:
        print(f"  {r['category']}: {r['cnt']}")

    last = await conn.fetchrow("SELECT scraped_at, last_seen_at FROM articles ORDER BY scraped_at DESC LIMIT 1")
    print(f"Ultimo scraped_at: {last['scraped_at']}")
    print(f"Ultimo last_seen_at: {last['last_seen_at']}")

    recent = await conn.fetchval("SELECT COUNT(*) FROM articles WHERE scraped_at > NOW() - INTERVAL '24 hours'")
    print(f"Articoli nelle ultime 24h: {recent}")

    sold = await conn.fetchval("SELECT COUNT(*) FROM articles WHERE status = 'sold'")
    deleted = await conn.fetchval("SELECT COUNT(*) FROM articles WHERE status = 'deleted'")
    active = await conn.fetchval("SELECT COUNT(*) FROM articles WHERE status = 'active'")
    print(f"Attivi: {active} | Venduti: {sold} | Eliminati: {deleted}")

    await conn.close()

asyncio.run(check())
