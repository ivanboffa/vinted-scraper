"""
Continuous scheduler — runs scraper and status checker in an infinite loop.

Job schedule:
  - Full scrape   every 6 hours  (discovery of new articles)
  - Status check  every 3 hours  (detect sold / deleted articles)
  - Sold finder   every 12 hours (reclassify deleted → sold + seller scan)

The jobs share a mutex so they never overlap.
On any unhandled exception the failing job waits 30 minutes before retrying.
The loop terminates cleanly on SIGTERM or KeyboardInterrupt.
"""

import asyncio
import logging
import signal
import sys
import time

import config
from .api_client import VintedAPIClient
from .async_scraper import run_full_scrape
from .categories import fetch_all_categories, filter_categories
from .db_manager import AsyncDatabaseManager
from .sold_finder import run_sold_finder
from .status_checker import run_status_check

logger = logging.getLogger(__name__)

# ── Intervals ─────────────────────────────────────────────────────────────
_SCRAPE_INTERVAL  = int(config.__dict__.get("SCRAPE_INTERVAL_H",  6)) * 3600
_STATUS_INTERVAL  = int(config.__dict__.get("STATUS_INTERVAL_H",  3)) * 3600
_SOLD_INTERVAL    = int(config.__dict__.get("SOLD_INTERVAL_H",   12)) * 3600
_ERROR_COOLDOWN   = 30 * 60         # 30 minutes after an unhandled exception


# ── Shutdown event ─────────────────────────────────────────────────────────

def _install_signal_handlers(shutdown: asyncio.Event) -> None:
    """Register SIGTERM / SIGINT handlers that set the shutdown event."""
    loop = asyncio.get_running_loop()   # get_event_loop() deprecated in 3.10+

    def _set() -> None:
        if not shutdown.is_set():
            logger.info("Shutdown signal received — finishing current job…")
            shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _set)                   # Unix / macOS
        except (NotImplementedError, AttributeError, OSError):
            # Windows: add_signal_handler not supported; SIGTERM also not catchable
            try:
                signal.signal(sig, lambda *_: _set())
            except OSError:
                pass   # SIGTERM on Windows — silently skip


# ── Sleep helper ───────────────────────────────────────────────────────────

async def _sleep_until(deadline: float, shutdown: asyncio.Event) -> None:
    """
    Sleep until `deadline` (monotonic clock) OR until shutdown fires — whichever
    comes first. Uses asyncio.wait_for so the wake-up is immediate, not polled.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return
    try:
        await asyncio.wait_for(shutdown.wait(), timeout=remaining)
    except asyncio.TimeoutError:
        pass   # deadline reached — normal exit


# ── Job runners ────────────────────────────────────────────────────────────

async def _run_scrape(client: VintedAPIClient, db: AsyncDatabaseManager) -> None:
    """Fetch categories + run full scrape, log summary."""
    logger.info("=== SCRAPE JOB START ===")
    t0 = time.monotonic()

    categories = await fetch_all_categories(client)
    categories = filter_categories(categories, config.EXCLUDE_CATEGORY_PREFIXES)
    logger.info("Scraping %d categories", len(categories))

    stats = await run_full_scrape(client, db, categories)

    elapsed = round(time.monotonic() - t0, 1)
    logger.info(
        "=== SCRAPE JOB DONE in %.0fs — %s ===",
        elapsed, stats.summary(),
    )


async def _run_status(client: VintedAPIClient, db: AsyncDatabaseManager) -> None:
    """Run status check, log summary."""
    logger.info("=== STATUS CHECK START ===")
    t0 = time.monotonic()

    result = await run_status_check(client, db)

    elapsed = round(time.monotonic() - t0, 1)
    logger.info(
        "=== STATUS CHECK DONE in %.0fs — "
        "checked=%d sold=%d deleted=%d errors=%d engagement_updated=%d ===",
        elapsed,
        result["checked"], result["sold"],
        result["deleted"], result["errors"],
        result.get("engagement_updated", 0),
    )


async def _run_sold(client: VintedAPIClient, db: AsyncDatabaseManager) -> None:
    """Run sold finder (reclassify + seller scan), log summary."""
    logger.info("=== SOLD FINDER START ===")
    t0 = time.monotonic()

    results = await run_sold_finder(client, db)
    r = results.get("reclassify", {})
    s = results.get("seller_scan", {})

    elapsed = round(time.monotonic() - t0, 1)
    logger.info(
        "=== SOLD FINDER DONE in %.0fs — "
        "reclassified=%d confirmed_deleted=%d reactivated=%d | "
        "sellers=%d new_sold=%d ===",
        elapsed,
        r.get("reclassified_sold", 0), r.get("confirmed_deleted", 0),
        r.get("reactivated", 0),
        s.get("sellers_checked", 0), s.get("new_sold_inserted", 0),
    )


# ── Job loops ──────────────────────────────────────────────────────────────

async def _scrape_loop(
    client: VintedAPIClient,
    db: AsyncDatabaseManager,
    lock: asyncio.Lock,
    shutdown: asyncio.Event,
) -> None:
    """Run _run_scrape every SCRAPE_INTERVAL seconds."""
    next_run = time.monotonic()                 # run immediately on first tick

    while not shutdown.is_set():
        await _sleep_until(next_run, shutdown)
        if shutdown.is_set():
            break

        async with lock:                        # block if status check is running
            try:
                await _run_scrape(client, db)
                next_run = time.monotonic() + _SCRAPE_INTERVAL
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Scrape job failed: %s", exc)
                next_run = time.monotonic() + _ERROR_COOLDOWN

    logger.info("Scrape loop exiting.")


async def _status_loop(
    client: VintedAPIClient,
    db: AsyncDatabaseManager,
    lock: asyncio.Lock,
    shutdown: asyncio.Event,
) -> None:
    """Run _run_status every STATUS_INTERVAL seconds (first run after one interval)."""
    next_run = time.monotonic() + _STATUS_INTERVAL   # don't run immediately

    while not shutdown.is_set():
        await _sleep_until(next_run, shutdown)
        if shutdown.is_set():
            break

        async with lock:                        # block if scrape is running
            try:
                await _run_status(client, db)
                next_run = time.monotonic() + _STATUS_INTERVAL
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Status check job failed: %s", exc)
                next_run = time.monotonic() + _ERROR_COOLDOWN

    logger.info("Status loop exiting.")


async def _sold_loop(
    client: VintedAPIClient,
    db: AsyncDatabaseManager,
    lock: asyncio.Lock,
    shutdown: asyncio.Event,
) -> None:
    """Run sold finder every SOLD_INTERVAL seconds (first run after one interval)."""
    next_run = time.monotonic() + _SOLD_INTERVAL   # don't run immediately

    while not shutdown.is_set():
        await _sleep_until(next_run, shutdown)
        if shutdown.is_set():
            break

        async with lock:
            try:
                await _run_sold(client, db)
                next_run = time.monotonic() + _SOLD_INTERVAL
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Sold finder job failed: %s", exc)
                next_run = time.monotonic() + _ERROR_COOLDOWN

    logger.info("Sold finder loop exiting.")


# ── Public entry point ────────────────────────────────────────────────────

async def run_scheduler(db: AsyncDatabaseManager) -> None:
    """
    Start the continuous scrape + status-check scheduler.

    Creates a single VintedAPIClient, reused across all job runs.
    Runs until SIGTERM or KeyboardInterrupt.
    """
    shutdown = asyncio.Event()
    _install_signal_handlers(shutdown)

    logger.info(
        "Scheduler starting — scrape every %dh, status check every %dh, sold finder every %dh",
        _SCRAPE_INTERVAL // 3600,
        _STATUS_INTERVAL // 3600,
        _SOLD_INTERVAL   // 3600,
    )

    lock = asyncio.Lock()   # mutual exclusion between the two jobs

    async with VintedAPIClient(
        concurrency=config.CONCURRENCY,
        delay=config.REQUEST_DELAY,
    ) as client:

        scrape_task = asyncio.create_task(
            _scrape_loop(client, db, lock, shutdown),
            name="scrape-loop",
        )
        status_task = asyncio.create_task(
            _status_loop(client, db, lock, shutdown),
            name="status-loop",
        )
        sold_task = asyncio.create_task(
            _sold_loop(client, db, lock, shutdown),
            name="sold-loop",
        )

        try:
            await asyncio.gather(scrape_task, status_task, sold_task)
        except asyncio.CancelledError:
            pass
        finally:
            # Ensure all tasks are cancelled if one raises unexpectedly
            for task in (scrape_task, status_task, sold_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(scrape_task, status_task, sold_task, return_exceptions=True)
            logger.info("Scheduler stopped.")
