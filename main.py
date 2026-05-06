import argparse
import asyncio
import logging
import os
import sys

# Force unbuffered output so logs appear in real-time when piped.
# NOTE: must also run Python with -u flag (or PYTHONUNBUFFERED=1) for pipe targets.
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

import config
from src.api_client import VintedAPIClient
from src.async_scraper import run_full_scrape
from src.categories import fetch_all_categories, filter_categories
from src.db_manager import AsyncDatabaseManager
from src.scheduler import run_scheduler
from src.sold_finder import run_sold_finder
from src.status_checker import run_status_check

# Ensure logs/ exists before FileHandler is attached
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/scraper.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ── Shared setup ──────────────────────────────────────────────────────────

def _check_config() -> None:
    if not config.DATABASE_URL or "user:pass" in config.DATABASE_URL:
        logger.error("DATABASE_URL non configurato nel file .env")
        sys.exit(1)


async def _make_db() -> AsyncDatabaseManager:
    db = AsyncDatabaseManager(config.DATABASE_URL, ssl=config.DB_SSL)
    await db.connect()
    return db


# ── Mode handlers ─────────────────────────────────────────────────────────

async def _run_scrape() -> None:
    """Single scrape run — fetches all categories and inserts new articles."""
    db = await _make_db()
    try:
        async with VintedAPIClient(
            concurrency=config.CONCURRENCY,
            delay=config.REQUEST_DELAY,
        ) as client:
            logger.info("Fetching category list from Vinted…")
            categories = await fetch_all_categories(client)
            categories = filter_categories(categories, config.EXCLUDE_CATEGORY_PREFIXES)
            logger.info(
                "Scraping %d categories (excluded: %s)",
                len(categories), config.EXCLUDE_CATEGORY_PREFIXES,
            )
            stats = await run_full_scrape(client, db, categories)
            logger.info("DONE — %s", stats.summary())
            if stats.errors:
                logger.warning("First 10 errors:\n%s", "\n".join(stats.errors[:10]))
    except asyncio.CancelledError:
        logger.info("Scrape interrupted — flushing DB and closing…")
    finally:
        await db.close()


async def _run_check() -> None:
    """Single status-check run — marks sold/deleted articles in the DB."""
    db = await _make_db()
    try:
        async with VintedAPIClient(
            concurrency=config.CONCURRENCY,
            delay=config.REQUEST_DELAY,
        ) as client:
            result = await run_status_check(client, db, limit=2500, oldest_first=config.OLDEST_FIRST)
            logger.info(
                "DONE — checked=%d sold=%d deleted=%d errors=%d (%.1fs)",
                result["checked"], result["sold"],
                result["deleted"], result["errors"],
                result["duration_seconds"],
            )
    except asyncio.CancelledError:
        logger.info("Status check interrupted.")
    finally:
        await db.close()


async def _run_scheduler() -> None:
    """Continuous loop: scrape every 6h, status check every 3h."""
    db = await _make_db()
    try:
        await run_scheduler(db)
    finally:
        await db.close()


async def _run_sold_finder() -> None:
    """
    Due strategie per trovare articoli venduti:
      1. Reclassify: ri-controlla tutti i 'deleted' via web → trova i sold mal classificati
      2. Seller scan: guarda i profili dei venditori noti → trova sold mai visti
    """
    db = await _make_db()
    try:
        async with VintedAPIClient(
            concurrency=config.CONCURRENCY,
            delay=config.REQUEST_DELAY,
        ) as client:
            results = await run_sold_finder(client, db)
            r = results.get("reclassify", {})
            s = results.get("seller_scan", {})
            logger.info(
                "SOLD FINDER DONE — "
                "reclassified_sold=%d confirmed_deleted=%d reactivated=%d | "
                "sellers=%d new_sold=%d",
                r.get("reclassified_sold", 0), r.get("confirmed_deleted", 0),
                r.get("reactivated", 0),
                s.get("sellers_checked", 0), s.get("new_sold_inserted", 0),
            )
    except asyncio.CancelledError:
        logger.info("Sold finder interrupted.")
    finally:
        await db.close()


# ── CLI ───────────────────────────────────────────────────────────────────

_MODES = {
    "scrape":    _run_scrape,
    "check":     _run_check,
    "scheduler": _run_scheduler,
    "sold":      _run_sold_finder,
}


def _parse_args() -> str:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Vinted scraper — available modes: scrape | check | scheduler | sold",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=list(_MODES),
        default="scrape",
        help="Execution mode (default: scrape)",
    )
    return parser.parse_args().mode


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    _check_config()
    mode = _parse_args()
    logger.info("Starting in mode: %s", mode)
    try:
        asyncio.run(_MODES[mode]())
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
