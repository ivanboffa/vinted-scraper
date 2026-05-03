import asyncio, asyncpg, sys
sys.stdout.reconfigure(encoding="utf-8")
DATABASE_URL = "postgresql://postgres.nkjhqqqtfmewifckmyqn:Vinted2026!Secure%23db@aws-1-eu-central-1.pooler.supabase.com:5432/postgres"

async def q():
    conn = await asyncpg.connect(DATABASE_URL, ssl="require")

    # Distribuzione views_count
    r = await conn.fetchrow("""
        SELECT
            COUNT(*) FILTER (WHERE views_count IS NULL)  as null_v,
            COUNT(*) FILTER (WHERE views_count = 0)     as zero_v,
            COUNT(*) FILTER (WHERE views_count > 0)     as pos_v,
            MAX(views_count)                             as max_v,
            COUNT(*) FILTER (WHERE favourite_count IS NULL) as null_f,
            COUNT(*) FILTER (WHERE favourite_count = 0) as zero_f,
            COUNT(*) FILTER (WHERE favourite_count > 0) as pos_f,
            MAX(favourite_count)                        as max_f
        FROM articles
    """)
    print("views_count  — null:", r['null_v'], "| zero:", r['zero_v'], "| >0:", r['pos_v'], "| max:", r['max_v'])
    print("favourite_count — null:", r['null_f'], "| zero:", r['zero_f'], "| >0:", r['pos_f'], "| max:", r['max_f'])

    # Items con più preferiti
    top = await conn.fetch("SELECT vinted_id, title, views_count, favourite_count FROM articles WHERE favourite_count > 0 ORDER BY favourite_count DESC LIMIT 5")
    print("\nTop 5 per favourite_count:")
    for x in top:
        print(f"  {x['vinted_id']} | fav={x['favourite_count']} | views={x['views_count']} | {x['title'][:40]}")

    await conn.close()

asyncio.run(q())
