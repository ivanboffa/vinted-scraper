import asyncio
import asyncpg
import sys
sys.stdout.reconfigure(encoding="utf-8")

DATABASE_URL = "postgresql://postgres.nkjhqqqtfmewifckmyqn:Vinted2026!Secure%23db@aws-1-eu-central-1.pooler.supabase.com:5432/postgres"

async def q():
    conn = await asyncpg.connect(DATABASE_URL, ssl="require")

    rows = await conn.fetch("SELECT status, COUNT(*) as n FROM articles GROUP BY status ORDER BY n DESC")
    print("=== Status counts ===")
    for x in rows:
        print(f"  {x['status']}: {x['n']}")

    sold = await conn.fetch("SELECT vinted_id, title, sold_at FROM articles WHERE status='sold' ORDER BY sold_at DESC LIMIT 5")
    print(f"\nSold items (total {sum(r['n'] for r in rows if r['status']=='sold')}):")
    for s in sold:
        print(f"  {s['vinted_id']} | {s['title'][:40]} | sold_at: {s['sold_at']}")

    recent_deleted = await conn.fetch(
        "SELECT COUNT(*) as n FROM articles WHERE status='deleted' AND last_seen_at > NOW() - INTERVAL '2 hours'"
    )
    print(f"\nDeleted in last 2h: {recent_deleted[0]['n']}")

    await conn.close()

asyncio.run(q())
