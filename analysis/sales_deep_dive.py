"""
Extended sales analysis — deeper dives.
Sections:
  A. Price × Category matrix
  B. Size sold rates
  C. Seller activity buckets (derived from seller_id grouping in DB)
  D. Time-of-day / day-of-week if timestamps allow
  E. Title keyword analysis (simple word frequency)
  F. Favourites as predictor of sale
"""
import asyncio, sys, re
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

# This script lives in a subdirectory; add the repository root to sys.path so
# `config` and `src` resolve regardless of the working directory.
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from src.db_manager import AsyncDatabaseManager

async def main():
    db = AsyncDatabaseManager(config.DATABASE_URL, ssl=config.DB_SSL)
    await db.connect()
    async with db._pool.acquire() as conn:

        # ── BASE SOLD COUNT ───────────────────────────────────────
        sold_total = await conn.fetchval("SELECT COUNT(*) FROM articles WHERE status='sold'")
        active_total = await conn.fetchval("SELECT COUNT(*) FROM articles WHERE status='active'")
        print(f"\nBase: {sold_total:,} sold  |  {active_total:,} active\n")

        # ══════════════════════════════════════════════════════════
        # A. PRICE × CATEGORY MATRIX
        # ══════════════════════════════════════════════════════════
        print("="*65)
        print("A. PRICE × CATEGORY  — sold rate (min 100 items per cell)")
        print("="*65)
        rows = await conn.fetch("""
            SELECT category,
                   CASE
                     WHEN price < 10  THEN '€0-10'
                     WHEN price < 25  THEN '€10-25'
                     WHEN price < 50  THEN '€25-50'
                     ELSE '€50+'
                   END AS price_bucket,
                   SUM(CASE WHEN status='sold'   THEN 1 ELSE 0 END) AS sold,
                   SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END) AS total
            FROM articles
            WHERE status IN ('sold','active') AND price IS NOT NULL AND category IS NOT NULL
            GROUP BY category, price_bucket
            HAVING SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END) >= 100
            ORDER BY (SUM(CASE WHEN status='sold' THEN 1.0 ELSE 0 END) /
                      SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END)) DESC
            LIMIT 30
        """)
        for r in rows:
            rate = r['sold'] / r['total'] * 100
            bar = '█' * int(rate * 20)
            print(f"  {r['category'][:30]:30} {r['price_bucket']:7}  "
                  f"sold={r['sold']:3}  total={r['total']:5,}  rate={rate:.2f}%  {bar}")

        # ══════════════════════════════════════════════════════════
        # B. SIZE SOLD RATE
        # ══════════════════════════════════════════════════════════
        print(f"\n{'='*65}")
        print("B. SIZE  — sold rate (min 150 items)")
        print("="*65)
        rows = await conn.fetch("""
            SELECT size,
                   SUM(CASE WHEN status='sold'   THEN 1 ELSE 0 END) AS sold,
                   SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END) AS total
            FROM articles
            WHERE status IN ('sold','active') AND size IS NOT NULL AND size <> ''
            GROUP BY size
            HAVING SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END) >= 150
            ORDER BY (SUM(CASE WHEN status='sold' THEN 1.0 ELSE 0 END) /
                      SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END)) DESC
            LIMIT 25
        """)
        for r in rows:
            rate = r['sold'] / r['total'] * 100
            bar = '█' * int(rate * 20)
            print(f"  {r['size'][:20]:20}  sold={r['sold']:3}  total={r['total']:6,}  rate={rate:.2f}%  {bar}")

        # ══════════════════════════════════════════════════════════
        # C. SELLER ACTIVITY (bucket by # items they have in DB)
        # ══════════════════════════════════════════════════════════
        print(f"\n{'='*65}")
        print("C. SELLER ACTIVITY  — sold rate by seller's total items in DB")
        print("="*65)
        rows = await conn.fetch("""
            WITH seller_counts AS (
                SELECT url,
                       -- extract seller slug from url: https://www.vinted.it/member/{slug}/items/{id}
                       SPLIT_PART(url, '/', 5) AS seller_slug,
                       status
                FROM articles
                WHERE url IS NOT NULL
            ),
            seller_item_totals AS (
                SELECT seller_slug,
                       COUNT(*) AS items_in_db,
                       SUM(CASE WHEN status='sold' THEN 1 ELSE 0 END) AS sold_count
                FROM seller_counts
                GROUP BY seller_slug
            )
            SELECT
                CASE
                  WHEN items_in_db = 1     THEN '1 item'
                  WHEN items_in_db <= 5    THEN '2-5 items'
                  WHEN items_in_db <= 20   THEN '6-20 items'
                  WHEN items_in_db <= 50   THEN '21-50 items'
                  WHEN items_in_db <= 100  THEN '51-100 items'
                  ELSE '100+ items'
                END AS bucket,
                COUNT(*) AS sellers,
                SUM(items_in_db) AS total_items,
                SUM(sold_count) AS sold_items,
                MIN(items_in_db) AS min_items
            FROM seller_item_totals
            GROUP BY bucket
            ORDER BY MIN(items_in_db)
        """)
        for r in rows:
            rate = r['sold_items'] / r['total_items'] * 100 if r['total_items'] else 0
            bar = '█' * int(rate * 20)
            print(f"  {r['bucket']:14}  sellers={r['sellers']:6,}  items={r['total_items']:7,}  "
                  f"sold={r['sold_items']:4}  rate={rate:.2f}%  {bar}")

        # ══════════════════════════════════════════════════════════
        # D. TIME OF DAY — when are items first seen? (proxy for listing time)
        # ══════════════════════════════════════════════════════════
        print(f"\n{'='*65}")
        print("D. LISTING HOUR (first_seen_at UTC)  — sold rate by hour")
        print("="*65)
        rows = await conn.fetch("""
            SELECT EXTRACT(HOUR FROM first_seen_at) AS hour,
                   SUM(CASE WHEN status='sold'   THEN 1 ELSE 0 END) AS sold,
                   SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END) AS total
            FROM articles
            WHERE status IN ('sold','active') AND first_seen_at IS NOT NULL
            GROUP BY hour
            ORDER BY hour
        """)
        for r in rows:
            rate = r['sold'] / r['total'] * 100 if r['total'] else 0
            bar = '█' * int(rate * 25)
            print(f"  {int(r['hour']):02d}:00  sold={r['sold']:4}  total={r['total']:6,}  rate={rate:.2f}%  {bar}")

        # ══════════════════════════════════════════════════════════
        # E. TITLE KEYWORDS — most common words in sold vs active
        # ══════════════════════════════════════════════════════════
        print(f"\n{'='*65}")
        print("E. TITLE KEYWORDS  — word frequency: sold vs active (top 30)")
        print("="*65)
        # Fetch titles
        sold_titles  = await conn.fetch("SELECT title FROM articles WHERE status='sold' AND title IS NOT NULL")
        actv_titles  = await conn.fetch("SELECT title FROM articles WHERE status='active' AND title IS NOT NULL LIMIT 20000")

        STOP = {
            'di','in','e','il','la','le','i','gli','un','una','per','da','con','a','del',
            'della','dei','delle','dello','su','al','alla','agli','nel','nella','nei',
            'nelle','che','è','si','non','the','and','of','de','en','et','du','des',
            '&','/','-','|','+'
        }

        def tokenize(rows):
            c = Counter()
            for r in rows:
                words = re.findall(r"[a-zA-ZàèéìòùÀÈÉÌÒÙ']+", r['title'].lower())
                c.update(w for w in words if w not in STOP and len(w) > 2)
            return c

        sold_words = tokenize(sold_titles)
        actv_words = tokenize(actv_titles)

        # Normalise by total count
        sold_n = sum(sold_words.values()) or 1
        actv_n = sum(actv_words.values()) or 1

        # Score = sold_freq / active_freq (words appearing in both, min 3 sold uses)
        scores = {}
        for word, cnt in sold_words.items():
            if cnt >= 3 and actv_words[word] >= 10:
                scores[word] = (cnt / sold_n) / (actv_words[word] / actv_n)

        print("  Words over-represented in SOLD titles (sold_freq / active_freq ratio):")
        for word, score in sorted(scores.items(), key=lambda x: -x[1])[:30]:
            print(f"    {word:20}  sold={sold_words[word]:3}  active={actv_words[word]:5}  ratio={score:.2f}x")

        # ══════════════════════════════════════════════════════════
        # F. FAVOURITES AS PREDICTOR
        # ══════════════════════════════════════════════════════════
        print(f"\n{'='*65}")
        print("F. FAVOURITES  — sold rate by favourite_count bucket")
        print("="*65)
        rows = await conn.fetch("""
            SELECT
                CASE
                  WHEN favourite_count = 0 THEN '0 favs'
                  WHEN favourite_count = 1 THEN '1 fav'
                  WHEN favourite_count <= 3 THEN '2-3 favs'
                  WHEN favourite_count <= 5 THEN '4-5 favs'
                  WHEN favourite_count <= 10 THEN '6-10 favs'
                  ELSE '11+ favs'
                END AS bucket,
                SUM(CASE WHEN status='sold'   THEN 1 ELSE 0 END) AS sold,
                SUM(CASE WHEN status IN ('sold','active') THEN 1 ELSE 0 END) AS total,
                MIN(favourite_count) AS min_fav
            FROM articles
            WHERE status IN ('sold','active') AND favourite_count IS NOT NULL
            GROUP BY bucket
            ORDER BY MIN(favourite_count)
        """)
        for r in rows:
            rate = r['sold'] / r['total'] * 100 if r['total'] else 0
            bar = '█' * int(rate * 20)
            print(f"  {r['bucket']:12}  sold={r['sold']:4}  total={r['total']:7,}  rate={rate:.2f}%  {bar}")

        # ══════════════════════════════════════════════════════════
        # G. SURVIVAL ANALYSIS — how long do active items survive?
        # ══════════════════════════════════════════════════════════
        print(f"\n{'='*65}")
        print("G. ACTIVE ITEM AGE DISTRIBUTION  — how old are unsold listings?")
        print("="*65)
        rows = await conn.fetch("""
            SELECT
                CASE
                  WHEN age_days < 1   THEN '< 1 day'
                  WHEN age_days < 3   THEN '1-3 days'
                  WHEN age_days < 7   THEN '3-7 days'
                  WHEN age_days < 14  THEN '1-2 weeks'
                  WHEN age_days < 30  THEN '2-4 weeks'
                  WHEN age_days < 60  THEN '1-2 months'
                  ELSE '2+ months'
                END AS age_bucket,
                COUNT(*) AS cnt,
                MIN(age_days) AS min_age
            FROM (
                SELECT EXTRACT(EPOCH FROM (NOW() - first_seen_at))/86400 AS age_days
                FROM articles
                WHERE status='active' AND first_seen_at IS NOT NULL
            ) t
            GROUP BY age_bucket
            ORDER BY MIN(age_days)
        """)
        total_active = sum(r['cnt'] for r in rows)
        for r in rows:
            pct = r['cnt'] / total_active * 100
            bar = '█' * int(pct / 2)
            print(f"  {r['age_bucket']:12}  n={r['cnt']:7,}  {pct:5.1f}%  {bar}")

    await db.close()

if __name__ == "__main__":
    asyncio.run(main())
