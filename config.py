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

# Status-check order: True = oldest articles first (backlog recovery), False = newest first (default)
OLDEST_FIRST = os.getenv("OLDEST_FIRST", "false").lower() == "true"

# Max articles to check per status-check run (hourly workflow uses 5000)
CHECK_LIMIT = int(os.getenv("CHECK_LIMIT", "5000"))

# Fresh-only mode: only check articles seen within FRESH_HOURS (for the 30-min fresh-check workflow)
FRESH_ONLY  = os.getenv("FRESH_ONLY", "false").lower() == "true"
FRESH_HOURS = int(os.getenv("FRESH_HOURS", "48"))

# Min-age mode: skip articles newer than MIN_AGE_HOURS (for mid-check targeting 48h–7d items)
MIN_AGE_HOURS = int(os.getenv("MIN_AGE_HOURS", "0"))

# Retention: delete active articles older than this many days (0 = disabled)
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "7"))

# Categories to exclude (case-insensitive prefix match on category path).
# Default: skip children and electronics, scrape only donna + uomo.
_raw_exclude = os.getenv("EXCLUDE_CATEGORIES", "bambini,elettronica,casa,intrattenimento,animali")
EXCLUDE_CATEGORY_PREFIXES: list[str] = [p.strip() for p in _raw_exclude.split(",") if p.strip()]
