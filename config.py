from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")

# Set to True for Supabase / Neon (cloud). Set False only for local PostgreSQL without SSL.
DB_SSL = os.getenv("DB_SSL", "true").lower() != "false"

# Scraper tuning
CONCURRENCY   = int(os.getenv("CONCURRENCY", "5"))
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.8"))

# Scheduler intervals (in hours)
SCRAPE_INTERVAL_H = int(os.getenv("SCRAPE_INTERVAL_H", "2"))
STATUS_INTERVAL_H = int(os.getenv("STATUS_INTERVAL_H", "3"))
SOLD_INTERVAL_H   = int(os.getenv("SOLD_INTERVAL_H",  "12"))

# Categories to exclude (case-insensitive prefix match on category path).
# Default: skip children and electronics, scrape only donna + uomo.
_raw_exclude = os.getenv("EXCLUDE_CATEGORIES", "bambini,elettronica,casa,intrattenimento,animali")
EXCLUDE_CATEGORY_PREFIXES: list[str] = [p.strip() for p in _raw_exclude.split(",") if p.strip()]
