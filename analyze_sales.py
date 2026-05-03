"""
Statistical analysis: what drives sales on Vinted?
Compares sold vs active articles across all available dimensions.
"""
import asyncio, sys, json, math
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import config
from src.db_manager import AsyncDatabaseManager

async def main():
    db = AsyncDatabaseManager(config.DATABASE_URL, ssl=config.DB_SSL)
    await db.connect()
    async with db._pool.acquire() as conn:

        # ── 1. BASE COUNTS ────────────────────────────────────────────────
        total  = await conn.fetchval("SELECT COUNT(*) FROM articles")
        sold   = await conn.fetchval("SELECT COUNT(*) FROM articles WHERE status='sold'")
        active = await conn.fetchval("SELECT COUNT(*) FROM articles WHERE status='active'")
        print(f"\n{'='*60}")
        print(f"BASE COUNTS")
        print(f"{'='*60}")
        print(f"  Total    : {total:>8,}")
        print(f"  Active   : {active:>8,}")
        print(f"  Sold     : {sold:>8,}  ({sold/total*100:.2f}% of total)")

        # ── 2. PRICE DISTRIBUTION ─────────────────────────────────────────
        print(f"\n{'='*60}")
        print("PRICE (EUR)  — sold vs active")
        print(f"{'='*60}")
        rows = await conn.fetch("""
            SELECT status,
                   ROUND(AVG(price)::numeric, 2)    AS avg,
                   ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price)::numeric, 2) AS median,
                   ROUND(MIN(price)::numeric, 2)    AS min,
                   ROUND(MAX(price)::numeric, 2)    AS max,
                   ROUND(STDDEV(price)::numeric, 2) AS stddev
            FROM articles
            WHERE status IN ('sold','active') AND price IS NOT NULL
            GROUP BY status
        """)
        for r in rows:
            print(f"  [{r['status']:6}]  avg={r['avg']}  median={r['median']}"
                  f"  min={r['min']}  max={r['max']}  stddev={r['stddev']}")

        # Price buckets
        print("\n  Price buckets (sold rate per bracket):")
        rows = await conn.fetch("""
            SELECT bracket,
                   SUM(CASE WHEN status='sold'   THEN 1 ELSE 0 END) AS sold,
                   SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active
            FROM (
              SELECT status,
                CASE
                  WHEN price < 5   THEN '< €5'
                  WHEN price < 10  THEN '€5-10'
                  WHEN price < 20  THEN '€10-20'
                  WHEN price < 35  THEN '€20-35'
                  WHEN price < 50  THEN '€35-50'
                  WHEN price < 100 THEN '€50-100'
                  ELSE '> €100'
                END AS bracket
              FROM articles
              WHERE status IN ('sold','active') AND price IS NOT NULL
            ) t
            GROUP BY bracket
            ORDER BY MIN(CASE bracket
              WHEN '< €5'    THEN 1 WHEN '€5-10'   THEN 2 WHEN '€10-20'  THEN 3
              WHEN '€20-35'  THEN 4 WHEN '€35-50'  THEN 5 WHEN '€50-100' THEN 6
              ELSE 7 END)
        """)
        for r in rows:
            tot = r['sold'] + r['active']
            rate = r['sold'] / tot * 100 if tot else 0
            bar = '█' * int(rate * 3)
            print(f"    {r['bracket']:10}  sold={r['sold']:5,}  active={r['active']:7,}  "
                  f"rate={rate:5.2f}%  {bar}")

        # ── 3. CATEGORY ───────────────────────────────────────────────────
        print(f"\n{'='*60}")
        print("CATEGORY  — sold rate (min 50 items)")
        print(f"{'='*60}")
        rows = await conn.fetch("""
            SELECT category,
                   SUM(CASE WHEN status='sold'   THEN 1 ELSE 0 END) AS sold,
                   SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END) AS total
            FROM articles
            WHERE status IN ('sold','active') AND category IS NOT NULL AND category <> ''
            GROUP BY category
            HAVING SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END) >= 50
            ORDER BY (SUM(CASE WHEN status='sold' THEN 1.0 ELSE 0 END) /
                      SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END)) DESC
            LIMIT 20
        """)
        for r in rows:
            rate = r['sold'] / r['total'] * 100
            bar = '█' * int(rate * 4)
            print(f"  {r['category'][:35]:35}  sold={r['sold']:4}  total={r['total']:6,}  "
                  f"rate={rate:.2f}%  {bar}")

        # ── 4. CONDITION ──────────────────────────────────────────────────
        print(f"\n{'='*60}")
        print("CONDITION  — sold rate")
        print(f"{'='*60}")
        rows = await conn.fetch("""
            SELECT condition,
                   SUM(CASE WHEN status='sold'   THEN 1 ELSE 0 END) AS sold,
                   SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END) AS total
            FROM articles
            WHERE status IN ('sold','active') AND condition IS NOT NULL AND condition <> ''
            GROUP BY condition
            HAVING SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END) >= 30
            ORDER BY sold DESC
        """)
        for r in rows:
            rate = r['sold'] / r['total'] * 100
            bar = '█' * int(rate * 5)
            print(f"  {r['condition'][:30]:30}  sold={r['sold']:4}  total={r['total']:6,}  "
                  f"rate={rate:.2f}%  {bar}")

        # ── 5. BRAND ──────────────────────────────────────────────────────
        print(f"\n{'='*60}")
        print("TOP BRANDS  — sold rate (min 100 items)")
        print(f"{'='*60}")
        rows = await conn.fetch("""
            SELECT brand,
                   SUM(CASE WHEN status='sold'   THEN 1 ELSE 0 END) AS sold,
                   SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END) AS total
            FROM articles
            WHERE status IN ('sold','active') AND brand IS NOT NULL AND brand <> ''
              AND LOWER(brand) NOT IN ('no brand','senza marca','no marque','sin marca',
                                       'pas de marque','kein brand','no brand/unbranded','')
            GROUP BY brand
            HAVING SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END) >= 100
            ORDER BY (SUM(CASE WHEN status='sold' THEN 1.0 ELSE 0 END) /
                      NULLIF(SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END),0)) DESC
            LIMIT 25
        """)
        for r in rows:
            rate = r['sold'] / r['total'] * 100
            bar = '█' * int(rate * 5)
            print(f"  {r['brand'][:25]:25}  sold={r['sold']:4}  total={r['total']:6,}  "
                  f"rate={rate:.2f}%  {bar}")

        # ── 6. PHOTO COUNT ────────────────────────────────────────────────
        print(f"\n{'='*60}")
        print("PHOTO COUNT  — sold rate")
        print(f"{'='*60}")
        rows = await conn.fetch("""
            SELECT photo_count,
                   SUM(CASE WHEN status='sold'   THEN 1 ELSE 0 END) AS sold,
                   SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END) AS total
            FROM articles
            WHERE status IN ('sold','active') AND photo_count IS NOT NULL
            GROUP BY photo_count
            HAVING SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END) >= 50
            ORDER BY photo_count
        """)
        for r in rows:
            rate = r['sold'] / r['total'] * 100
            bar = '█' * int(rate * 5)
            print(f"  photos={r['photo_count']:2}  sold={r['sold']:4}  total={r['total']:6,}  "
                  f"rate={rate:.2f}%  {bar}")

        # ── 7. SELLER BEHAVIOR ────────────────────────────────────────────
        print(f"\n{'='*60}")
        print("SELLER ITEM COUNT  — sold rate")
        print(f"{'='*60}")
        rows = await conn.fetch("""
            SELECT
                CASE
                  WHEN seller_item_count = 1      THEN '1 item'
                  WHEN seller_item_count <= 5     THEN '2-5 items'
                  WHEN seller_item_count <= 20    THEN '6-20 items'
                  WHEN seller_item_count <= 50    THEN '21-50 items'
                  WHEN seller_item_count <= 100   THEN '51-100 items'
                  ELSE '100+ items'
                END AS bucket,
                SUM(CASE WHEN status='sold'   THEN 1 ELSE 0 END) AS sold,
                SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END) AS total
            FROM articles
            WHERE status IN ('sold','active') AND seller_item_count IS NOT NULL
            GROUP BY bucket
            ORDER BY MIN(seller_item_count)
        """)
        for r in rows:
            rate = r['sold'] / r['total'] * 100 if r['total'] else 0
            bar = '█' * int(rate * 5)
            print(f"  {r['bucket']:15}  sold={r['sold']:4}  total={r['total']:6,}  "
                  f"rate={rate:.2f}%  {bar}")

        # ── 8. TIME ON PLATFORM ───────────────────────────────────────────
        print(f"\n{'='*60}")
        print("TIME TO SELL  — how long before sold (days since first_seen_at)")
        print(f"{'='*60}")
        rows = await conn.fetch("""
            SELECT
                ROUND(AVG(EXTRACT(EPOCH FROM (sold_at - first_seen_at))/86400)::numeric, 1)
                    AS avg_days_to_sell,
                ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP
                    (ORDER BY EXTRACT(EPOCH FROM (sold_at - first_seen_at))/86400)::numeric, 1)
                    AS median_days,
                ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP
                    (ORDER BY EXTRACT(EPOCH FROM (sold_at - first_seen_at))/86400)::numeric, 1)
                    AS p25_days,
                ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP
                    (ORDER BY EXTRACT(EPOCH FROM (sold_at - first_seen_at))/86400)::numeric, 1)
                    AS p75_days
            FROM articles
            WHERE status='sold'
              AND sold_at IS NOT NULL AND first_seen_at IS NOT NULL
              AND sold_at >= first_seen_at
        """)
        for r in rows:
            print(f"  Avg: {r['avg_days_to_sell']} days  "
                  f"Median: {r['median_days']} days  "
                  f"P25: {r['p25_days']} days  P75: {r['p75_days']} days")

        # ── 9. VIEWS & FAVOURITES (where available) ───────────────────────
        print(f"\n{'='*60}")
        print("VIEWS & FAVOURITES  (items with data)")
        print(f"{'='*60}")
        rows = await conn.fetch("""
            SELECT status,
                   COUNT(*) AS n,
                   ROUND(AVG(views_count)::numeric, 1) AS avg_views,
                   ROUND(AVG(favourite_count)::numeric, 2) AS avg_favs,
                   ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY views_count)::numeric, 0)
                       AS med_views,
                   ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY favourite_count)::numeric, 0)
                       AS med_favs
            FROM articles
            WHERE status IN ('sold','active')
              AND views_count IS NOT NULL
            GROUP BY status
        """)
        for r in rows:
            print(f"  [{r['status']:6}] n={r['n']:,}  "
                  f"avg_views={r['avg_views']}  med_views={r['med_views']}  "
                  f"avg_favs={r['avg_favs']}  med_favs={r['med_favs']}")

        # ── 10. TITLE LENGTH ──────────────────────────────────────────────
        print(f"\n{'='*60}")
        print("TITLE LENGTH  — sold vs active")
        print(f"{'='*60}")
        rows = await conn.fetch("""
            SELECT status,
                   ROUND(AVG(LENGTH(title))::numeric, 1) AS avg_len,
                   ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY LENGTH(title))::numeric, 0)
                       AS median_len
            FROM articles
            WHERE status IN ('sold','active') AND title IS NOT NULL
            GROUP BY status
        """)
        for r in rows:
            print(f"  [{r['status']:6}]  avg_title_len={r['avg_len']}  "
                  f"median_title_len={r['median_len']}")

        # ── 11. MOST SOLD ARTICLES (examples) ────────────────────────────
        print(f"\n{'='*60}")
        print("EXAMPLE SOLD ARTICLES (top 20 by price)")
        print(f"{'='*60}")
        rows = await conn.fetch("""
            SELECT title, brand, price, category, condition, photo_count,
                   views_count, favourite_count,
                   ROUND(EXTRACT(EPOCH FROM (sold_at - first_seen_at))/3600, 1) AS hours_to_sell
            FROM articles
            WHERE status='sold' AND price IS NOT NULL
            ORDER BY price DESC
            LIMIT 20
        """)
        for r in rows:
            print(f"  €{r['price']:6.2f}  [{r['hours_to_sell'] or '?':>6}h]  "
                  f"{r['title'][:35]:35}  brand={r['brand'][:15]:15}  "
                  f"cat={r['category'][:20]:20}  cond={r['condition'][:15]:15}  "
                  f"photos={r['photo_count']}  views={r['views_count']}  favs={r['favourite_count']}")

    await db.close()

asyncio.run(main())
