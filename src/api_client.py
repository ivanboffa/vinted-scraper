"""
Vinted API client.

Uses curl_cffi to impersonate Chrome at the TLS level (JA3/JA4 fingerprint),
which defeats TLS-based bot detection (Cloudflare, DataDome, PerimeterX).
"""

import asyncio
import json as _json
import logging
import random
import re

from curl_cffi.requests import AsyncSession

from .anti_ban import (
    CircuitBreaker,
    TokenBucket,
    jitter_sleep,
    load_proxies,
    next_proxy,
    random_headers,
    remove_proxy,
)

logger = logging.getLogger(__name__)

VINTED_BASE = "https://www.vinted.it"
API_BASE    = f"{VINTED_BASE}/api/v2"

# Headers sent with every request.
# NO X-Requested-With — real Chrome fetch() does not include it and its
# presence is a well-known bot-detection signal.
_BASE_HEADERS = {
    "Accept":          "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.vinted.it/",
    "Origin":          "https://www.vinted.it",
    "Sec-Fetch-Dest":  "empty",
    "Sec-Fetch-Mode":  "cors",
    "Sec-Fetch-Site":  "same-origin",
    "Connection":      "keep-alive",
}

_CHROME_IMPERSONATE = "chrome136"   # must stay in sync with USER_AGENTS in anti_ban.py
_SESSION_REFRESH_EVERY = 200        # refresh cookies every N requests


def _extract_country(item: dict) -> str | None:
    """
    Extract the seller's ISO-3166-1 alpha-2 country code from a Vinted item dict.
    Checks multiple possible paths used across API versions.
    """
    # Direct field on item (rare)
    c = item.get("country_iso_code") or item.get("countryIsoCode")
    if c:
        return str(c).upper()[:2]

    # Nested under user object
    user = item.get("user") or {}
    c = user.get("country_iso_code") or user.get("countryIsoCode")
    if c:
        return str(c).upper()[:2]

    # Nested further: user.country or user.location
    location = user.get("country") or user.get("location") or {}
    if isinstance(location, dict):
        c = location.get("iso_code") or location.get("isoCode") or location.get("code")
        if c:
            return str(c).upper()[:2]

    return None


def _extract_vinted_created_at(item: dict):
    """
    Extract the original Vinted publication timestamp from an item dict.
    Returns a naive UTC datetime (no tzinfo), compatible with asyncpg
    TIMESTAMP WITHOUT TIME ZONE columns. Returns None if not found.
    """
    from datetime import datetime, timezone
    raw = (
        item.get("created_at_ts")
        or item.get("createdAtTs")
        or item.get("created_at")
        or item.get("createdAt")
    )
    if raw is None:
        return None
    # Unix epoch (int or float)
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc).replace(tzinfo=None)
        except (OSError, ValueError, OverflowError):
            return None
    # ISO string
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except ValueError:
            return None
    return None


def _parse_web_data(html: str):
    """
    Parse a Vinted item web page.

    Vinted uses Next.js App Router (RSC) — item status is embedded in
    self.__next_f.push([1, "..."]) chunks, NOT in __NEXT_DATA__.

    Returns (sold, views, favourites, vinted_created_at, country_iso_code):
      sold:              True = sold, False = not sold, None = cannot determine
      views, favourites: int if found in page data, else None
      vinted_created_at: datetime (UTC) or None  (rarely available on web)
      country_iso_code:  str ISO-3166-1 alpha-2   (rarely available on web)
    """
    views: int | None = None
    favourites: int | None = None
    vinted_created_at = None
    country_iso_code: str | None = None

    # ── Strategy 1: Next.js App Router RSC chunks ────────────────────────
    # Vinted migrated from Pages Router (__NEXT_DATA__) to App Router (RSC).
    # Item status is encoded in self.__next_f.push([1, "<escaped-json>"]) tags.
    # We concatenate all chunks and search for key fields.
    rsc_chunks = re.findall(
        r'self\.__next_f\.push\(\[1,(.*?)\]\)',
        html,
        re.DOTALL,
    )
    if rsc_chunks:
        rsc_text = "".join(rsc_chunks)
        # is_closed / isClosed
        m_closed = re.search(r'"is_closed"\s*:\s*(true|false)', rsc_text)
        if not m_closed:
            m_closed = re.search(r'"isClosed"\s*:\s*(true|false)', rsc_text)
        # item_closing_action
        m_action = re.search(r'"item_closing_action"\s*:\s*"([^"]*)"', rsc_text)
        if not m_action:
            m_action = re.search(r'"itemClosingAction"\s*:\s*"([^"]*)"', rsc_text)
        # can_buy (supplementary signal)
        m_can_buy = re.search(r'"can_buy"\s*:\s*(true|false)', rsc_text)

        if m_closed:
            is_closed = m_closed.group(1) == "true"
            closing_action = (m_action.group(1) if m_action else "").lower()
            if is_closed:
                # closed + action "sold" or empty → sold
                if closing_action in ("sold", ""):
                    return True, views, favourites, vinted_created_at, country_iso_code
                else:
                    return False, views, favourites, vinted_created_at, country_iso_code
            else:
                # not closed → still active
                return False, views, favourites, vinted_created_at, country_iso_code

    # ── Strategy 2: Legacy __NEXT_DATA__ (Pages Router — kept for older pages) ──
    m = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    if m:
        try:
            data = _json.loads(m.group(1))
            item = (
                data.get("props", {}).get("pageProps", {}).get("item")
                or data.get("props", {}).get("pageProps", {}).get("itemDto")
                or {}
            )
            if item:
                v = item.get("view_count") or item.get("views_count")
                f = item.get("favourite_count") or item.get("favorites_count")
                if isinstance(v, int):
                    views = v
                if isinstance(f, int):
                    favourites = f
                vinted_created_at = _extract_vinted_created_at(item)
                country_iso_code  = _extract_country(item)

                if item.get("is_sold") or item.get("isSold"):
                    return True, views, favourites, vinted_created_at, country_iso_code
                is_closed = item.get("is_closed") or item.get("isClosed")
                if is_closed:
                    action = (item.get("item_closing_action") or "").lower()
                    return action in ("sold", ""), views, favourites, vinted_created_at, country_iso_code
                if "is_sold" in item or "isSold" in item:
                    return False, views, favourites, vinted_created_at, country_iso_code
        except Exception:
            pass

    # ── Strategy 3: raw regex on full HTML ──────────────────────────────
    if re.search(r'"is_sold"\s*:\s*true', html) or re.search(r'"isSold"\s*:\s*true', html):
        return True, views, favourites, vinted_created_at, country_iso_code
    if re.search(r'"is_closed"\s*:\s*true', html):
        return True, views, favourites, vinted_created_at, country_iso_code
    if re.search(r'"is_sold"\s*:\s*false', html) or re.search(r'"isSold"\s*:\s*false', html):
        return False, views, favourites, vinted_created_at, country_iso_code

    # ── Strategy 4: Italian visible-text indicators ───────────────────────
    lower = html.lower()
    if "è stato venduto" in lower or "questo articolo è stato venduto" in lower:
        return True, views, favourites, vinted_created_at, country_iso_code

    return None, views, favourites, vinted_created_at, country_iso_code


def _extract_csrf(html: str) -> str | None:
    """Parse CSRF token from Vinted's homepage HTML."""
    # <meta name="csrf-token" content="...">
    for pattern in (
        r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']csrf-token["\']',
        r'"csrf_token"\s*:\s*"([^"]+)"',
    ):
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return None


class VintedAPIClient:
    """
    Async Vinted API client.

    Key properties:
    - curl_cffi impersonates Chrome 124 TLS stack (JA3/JA4 match)
    - TokenBucket limits global request rate (default 3 req/s)
    - Session rotates every 200 requests to refresh cookies + CSRF token
    - Per-request randomised headers (UA, Accept-Language, Sec-CH-UA)
    - Circuit breaker skips consistently-failing catalogs
    - Proxy rotation with automatic removal of bad proxies
    """

    def __init__(self, concurrency: int = 5, delay: float = 0.8):
        self.concurrency = concurrency
        self.delay = delay
        self._session: AsyncSession | None = None
        self._csrf_token: str | None = None
        self._semaphore = asyncio.Semaphore(concurrency)
        self._refresh_lock = asyncio.Lock()
        self._rate = TokenBucket(rate=3.0, capacity=float(concurrency))
        self._circuit = CircuitBreaker(threshold=3, reset_after=300.0)
        self._request_count = 0
        self._session_gen = 0   # incremented on every session creation

    async def __aenter__(self):
        load_proxies()
        await self._new_session()
        return self

    async def __aexit__(self, *_):
        if self._session:
            await self._session.close()

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def _new_session(self):
        if self._session:
            await self._session.close()
        # impersonate= makes curl_cffi mimic Chrome's TLS ClientHello,
        # HTTP/2 settings, and ALPN — invisible to bot-detection fingerprinting
        self._session = AsyncSession(impersonate=_CHROME_IMPERSONATE)
        self._session_gen += 1
        await self._init_cookies()

    async def _init_cookies(self):
        """
        Visit the homepage to:
          1. Acquire session cookies (required by the API)
          2. Extract the CSRF token
        """
        try:
            headers = random_headers(_BASE_HEADERS)
            # homepage needs different Accept header
            headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            headers.pop("Sec-Fetch-Mode", None)   # navigation request, not cors
            resp = await self._session.get(
                VINTED_BASE,
                headers=headers,
                timeout=20,
            )
            self._csrf_token = _extract_csrf(resp.text)
            logger.debug(
                "Session refreshed — CSRF: %s",
                "found" if self._csrf_token else "not found",
            )
        except Exception as exc:
            logger.warning("Cookie init failed: %s", exc)

    async def _maybe_refresh_session(self):
        """Rotate session every N requests. Called before acquiring the semaphore."""
        self._request_count += 1
        if self._request_count % _SESSION_REFRESH_EVERY != 0:
            return
        # Skip if a refresh is already in progress (another coroutine beat us to it)
        if self._refresh_lock.locked():
            return
        async with self._refresh_lock:
            # Re-check inside the lock: another coroutine may have just refreshed
            if self._request_count % _SESSION_REFRESH_EVERY != 0:
                return
            logger.info("Rotating session after %d requests…", self._request_count)
            await self._new_session()

    def _api_headers(self) -> dict:
        h = random_headers(_BASE_HEADERS)
        if self._csrf_token:
            h["X-CSRF-Token"] = self._csrf_token
        return h

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_item(self, vinted_id: str) -> tuple[int, dict | None]:
        """
        Fetch a single item by ID.
        Returns (http_status_code, parsed_json | None).
        The caller decides how to interpret the status — no retries here
        so the status checker can apply its own back-off logic.
        """
        url = f"{API_BASE}/items/{vinted_id}"
        await self._maybe_refresh_session()
        await self._rate.acquire()
        proxy = next_proxy()
        proxies = {"https": proxy, "http": proxy} if proxy else None
        try:
            resp = await self._session.get(
                url,
                headers=self._api_headers(),
                proxies=proxies,
                timeout=20,
            )
            body = None
            if resp.status_code == 200:
                try:
                    body = resp.json()
                except Exception:
                    pass
            return resp.status_code, body
        except Exception as exc:
            logger.warning("get_item %s error: %s", vinted_id, exc)
            if proxy and ("proxy" in str(exc).lower() or "connect" in str(exc).lower()):
                remove_proxy(proxy)
            return 0, None

    async def check_item_web(
        self, vinted_id: str
    ) -> tuple[str, int | None, int | None, object, str | None]:
        """
        Fetch the Vinted item *web page* (not API) to distinguish sold from deleted
        and extract engagement data and enrichment fields from __NEXT_DATA__.

        The API returns 404 for both sold items and items removed by the seller.
        The website still serves sold items (with embedded sold status in __NEXT_DATA__),
        while truly deleted items also 404 on the website.

        Returns: (status, views, favourites, vinted_created_at, country_iso_code)
          status:            'sold' | 'deleted' | 'active' | 'unknown'
          views, favourites: int if found in page data, else None
          vinted_created_at: datetime (UTC) if found, else None
          country_iso_code:  str ISO-3166-1 alpha-2 (e.g. "IT") or None
        """
        _NONE5 = ("unknown", None, None, None, None)
        url = f"{VINTED_BASE}/items/{vinted_id}"
        headers = random_headers({
            "Accept":          "text/html,application/xhtml+xml,*/*;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer":         VINTED_BASE + "/",
            "Sec-Fetch-Dest":  "document",
            "Sec-Fetch-Mode":  "navigate",
            "Sec-Fetch-Site":  "same-origin",
            "Connection":      "keep-alive",
        })
        await self._rate.acquire()
        proxy = next_proxy()
        proxies = {"https": proxy, "http": proxy} if proxy else None
        for attempt in range(2):   # 1 retry on rate-limit
            try:
                resp = await self._session.get(
                    url,
                    headers=headers,
                    proxies=proxies,
                    timeout=20,
                    allow_redirects=True,
                )
                if resp.status_code == 404:
                    return "deleted", None, None, None, None
                if resp.status_code in (403, 429, 503):
                    if attempt == 0:
                        await asyncio.sleep(3 + random.uniform(0, 2))
                        continue
                    return _NONE5
                if resp.status_code == 200:
                    sold, views, favs, created_at, country = _parse_web_data(resp.text)
                    if sold is True:
                        return "sold", views, favs, created_at, country
                    if sold is False:
                        return "active", views, favs, created_at, country
                    return "unknown", views, favs, created_at, country
                return _NONE5
            except Exception as exc:
                logger.warning("check_item_web %s error: %s", vinted_id, exc)
                if proxy and ("proxy" in str(exc).lower() or "connect" in str(exc).lower()):
                    remove_proxy(proxy)
                return _NONE5
        return _NONE5

    async def get_categories(self) -> list[dict]:
        url = f"{API_BASE}/catalogs"
        try:
            await self._rate.acquire()
            async with self._semaphore:
                await jitter_sleep(self.delay)
                resp = await self._session.get(
                    url,
                    headers=self._api_headers(),
                    params={"per_page": 500},
                    timeout=20,
                )
                if resp.status_code != 200:
                    logger.warning("get_categories HTTP %s", resp.status_code)
                    return []
                return resp.json().get("catalogs", [])
        except Exception as exc:
            logger.error("get_categories error: %s", exc)
            return []

    async def get_items_page(
        self,
        catalog_id: int,
        page: int = 1,
        per_page: int = 96,
        retries: int = 4,
    ) -> dict:
        if self._circuit.is_open(catalog_id):
            return {}

        url = f"{API_BASE}/catalog/items"
        params = {
            "catalog_ids[]": catalog_id,
            "page":          page,
            "per_page":      per_page,
            "order":         "newest_first",
        }

        for attempt in range(1, retries + 1):
            await self._maybe_refresh_session()
            await self._rate.acquire()       # global rate cap

            proxy = next_proxy()
            proxies = {"https": proxy, "http": proxy} if proxy else None

            try:
                async with self._semaphore:
                    await jitter_sleep(self.delay, spread=0.6)
                    resp = await self._session.get(
                        url,
                        headers=self._api_headers(),
                        params=params,
                        proxies=proxies,
                        timeout=30,
                    )
                    status = resp.status_code

                    if status == 200:
                        self._circuit.record_success(catalog_id)
                        return resp.json()

                    if status == 429:
                        retry_after = int(resp.headers.get("Retry-After", 15 * attempt))
                        logger.warning("429 catalog %s p%s — cooling %ss", catalog_id, page, retry_after)
                        self._circuit.record_failure(catalog_id)
                        await asyncio.sleep(retry_after)
                        continue

                    if status in (403, 401):
                        gen_before = self._session_gen
                        async with self._refresh_lock:
                            # Only create a new session if no other task already did it
                            if self._session_gen == gen_before:
                                logger.warning("HTTP %s catalog %s — rotating session", status, catalog_id)
                                await self._new_session()
                            else:
                                logger.debug("HTTP %s catalog %s — session already rotated by peer", status, catalog_id)
                        self._circuit.record_failure(catalog_id)
                        backoff = min(10 * (2 ** (attempt - 1)) + random.uniform(0, 5), 90)
                        await asyncio.sleep(backoff)
                        continue

                    if status == 503:
                        wait = 30 * attempt
                        logger.warning("503 catalog %s — waiting %ss", catalog_id, wait)
                        await asyncio.sleep(wait)
                        continue

                    logger.warning("HTTP %s catalog %s p%s", status, catalog_id, page)
                    return {}

            except Exception as exc:
                err = str(exc)
                logger.warning("Request error catalog %s p%s attempt %s: %s", catalog_id, page, attempt, err)
                if proxy and ("proxy" in err.lower() or "connect" in err.lower()):
                    remove_proxy(proxy)

            self._circuit.record_failure(catalog_id)
            await asyncio.sleep(min((2 ** attempt) + random.uniform(0, 1), 60))

        logger.error("Gave up on catalog %s p%s", catalog_id, page)
        return {}
