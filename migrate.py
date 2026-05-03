"""One-shot migration — adds lifecycle columns to the articles table."""
import asyncio
import os
import sys
from dotenv import load_dotenv
import asyncpg
import ssl as ssl_module

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "")

MIGRATION = """
ALTER TABLE articles
    ADD COLUMN IF NOT EXISTS status            TEXT      NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS first_seen_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS last_seen_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS sold_at           TIMESTAMP NULL,
    ADD COLUMN IF NOT EXISTS vinted_created_at TIMESTAMP NULL,
    ADD COLUMN IF NOT EXISTS photo_count       INT       NULL,
    ADD COLUMN IF NOT EXISTS views_count       INT       NULL,
    ADD COLUMN IF NOT EXISTS favourite_count   INT       NULL;
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_articles_category      ON articles (category);",
    "CREATE INDEX IF NOT EXISTS idx_articles_brand         ON articles (brand);",
    "CREATE INDEX IF NOT EXISTS idx_articles_price         ON articles (price);",
    "CREATE INDEX IF NOT EXISTS idx_articles_status        ON articles (status);",
    "CREATE INDEX IF NOT EXISTS idx_articles_first_seen_at ON articles (first_seen_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_articles_last_seen_at  ON articles (last_seen_at DESC);",
]

async def main():
    print(f"Connecting to database...")
    ctx = ssl_module.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl_module.CERT_NONE

    try:
        conn = await asyncpg.connect(DATABASE_URL, ssl=ctx)
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    print("Connected. Running migration...")
    try:
        await conn.execute(MIGRATION)
        print("  ALTER TABLE — OK")
        for idx_sql in INDEXES:
            await conn.execute(idx_sql)
            print(f"  {idx_sql[:60]}... — OK")
        print("\nMigration complete. You can now run: python main.py scrape")
    except Exception as e:
        print(f"Migration error: {e}")
        sys.exit(1)
    finally:
        await conn.close()

asyncio.run(main())
