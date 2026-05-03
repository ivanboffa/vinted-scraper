"""
Anti-ban utilities: user-agent rotation, header randomization,
jittered delays, proxy rotation, circuit breaking, global rate limiter.
"""

import asyncio
import logging
import random
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# User-agent pool — must match the curl_cffi impersonate version
# ---------------------------------------------------------------------------

USER_AGENTS: list[str] = [
    # Chrome 136 — matches impersonate="chrome136" (March 2025, current stable)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.6778.205 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    # Edge 136 (Chromium-based, same TLS fingerprint class)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
]

_SEC_CH_UA_MAP: dict[str, str] = {
    "Chrome/136": '"Chromium";v="136", "Google Chrome";v="136", "Not-A.Brand";v="99"',
    "Edg/136":    '"Microsoft Edge";v="136", "Chromium";v="136", "Not-A.Brand";v="99"',
}

_ACCEPT_LANGUAGES = [
    "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "it-IT,it;q=0.9,en;q=0.8",
    "it,en-US;q=0.9,en;q=0.8",
    "it-IT,it;q=0.8,en-US;q=0.5,en;q=0.3",
]


def random_headers(base: dict | None = None) -> dict:
    """Return headers with randomized UA, Accept-Language, and matching Sec-CH-UA."""
    ua = random.choice(USER_AGENTS)
    h = dict(base or {})
    h["User-Agent"] = ua
    h["Accept-Language"] = random.choice(_ACCEPT_LANGUAGES)

    for key, sec_ua in _SEC_CH_UA_MAP.items():
        if key in ua:
            h["Sec-CH-UA"] = sec_ua
            h["Sec-CH-UA-Mobile"] = "?0"
            h["Sec-CH-UA-Platform"] = (
                '"Windows"' if "Windows" in ua else
                '"macOS"'   if "Macintosh" in ua else
                '"Linux"'
            )
            break
    else:
        h.pop("Sec-CH-UA", None)
        h.pop("Sec-CH-UA-Mobile", None)
        h.pop("Sec-CH-UA-Platform", None)

    return h


# ---------------------------------------------------------------------------
# Jittered delay
# ---------------------------------------------------------------------------

async def jitter_sleep(base: float, spread: float = 0.5):
    await asyncio.sleep(max(0.1, base + random.uniform(-spread * 0.5, spread)))


# ---------------------------------------------------------------------------
# Global token-bucket rate limiter
# Caps the total number of requests/second across all concurrent workers.
# ---------------------------------------------------------------------------

class TokenBucket:
    """
    Leaky-bucket rate limiter.
    Acquire a token before every HTTP request to stay under the global rate cap.
    """

    def __init__(self, rate: float = 3.0, capacity: float = 5.0):
        """
        Args:
            rate:     tokens refilled per second (= max sustained req/s)
            capacity: burst capacity (tokens can accumulate up to this limit)
        """
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last = now

            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self.rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


# ---------------------------------------------------------------------------
# Proxy rotation
# ---------------------------------------------------------------------------

_PROXY_FILE = Path(__file__).parent.parent / "proxies.txt"
_proxy_pool: list[str] = []
_proxy_index = 0


def load_proxies() -> list[str]:
    global _proxy_pool
    if _PROXY_FILE.exists():
        lines = [
            l.strip()
            for l in _PROXY_FILE.read_text().splitlines()
            if l.strip() and not l.startswith("#")
        ]
        _proxy_pool = lines
        logger.info("Loaded %d proxies", len(lines))
    else:
        _proxy_pool = []
        logger.info("No proxies.txt — running without proxies")
    return _proxy_pool


def next_proxy() -> str | None:
    global _proxy_index
    if not _proxy_pool:
        return None
    proxy = _proxy_pool[_proxy_index % len(_proxy_pool)]
    _proxy_index += 1
    return proxy


def remove_proxy(proxy: str):
    if proxy in _proxy_pool:
        _proxy_pool.remove(proxy)
        logger.warning("Removed bad proxy %s (%d left)", proxy, len(_proxy_pool))


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class CircuitBreaker:
    def __init__(self, threshold: int = 5, reset_after: float = 120.0):
        self.threshold = threshold
        self.reset_after = reset_after
        self._failures: dict[int, int] = {}
        self._opened_at: dict[int, float] = {}

    def record_success(self, key: int):
        self._failures.pop(key, None)
        self._opened_at.pop(key, None)

    def record_failure(self, key: int):
        self._failures[key] = self._failures.get(key, 0) + 1
        if self._failures[key] >= self.threshold:
            self._opened_at.setdefault(key, time.monotonic())

    def is_open(self, key: int) -> bool:
        if key not in self._opened_at:
            return False
        if time.monotonic() - self._opened_at[key] >= self.reset_after:
            logger.info("Circuit reset for catalog %s", key)
            self._failures.pop(key, None)
            self._opened_at.pop(key, None)
            return False
        return True
