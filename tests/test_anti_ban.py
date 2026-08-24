"""Rate limiting and circuit breaking — what keeps the scraper under the radar."""
import asyncio
import time

from src.anti_ban import CircuitBreaker, TokenBucket, random_headers


def test_burst_is_capped_by_capacity():
    """Capacity tokens are free; the next one has to wait for a refill."""
    bucket = TokenBucket(rate=100.0, capacity=3.0)

    async def drain():
        start = time.monotonic()
        for _ in range(3):
            await bucket.acquire()
        assert time.monotonic() - start < 0.05, "burst should not be throttled"
        await bucket.acquire()
        return time.monotonic() - start

    assert asyncio.run(drain()) >= 0.005, "fourth request should wait for a refill"


def test_circuit_opens_only_at_threshold():
    breaker = CircuitBreaker(threshold=3, reset_after=60.0)
    for _ in range(2):
        breaker.record_failure(key=4)
    assert breaker.is_open(4) is False
    breaker.record_failure(key=4)
    assert breaker.is_open(4) is True


def test_circuit_is_per_key():
    """One bad category must not stop the others."""
    breaker = CircuitBreaker(threshold=1, reset_after=60.0)
    breaker.record_failure(key=4)
    assert breaker.is_open(4) is True
    assert breaker.is_open(9) is False


def test_success_closes_the_circuit():
    breaker = CircuitBreaker(threshold=1, reset_after=60.0)
    breaker.record_failure(key=4)
    breaker.record_success(key=4)
    assert breaker.is_open(4) is False


def test_circuit_closes_again_after_the_reset_window():
    breaker = CircuitBreaker(threshold=1, reset_after=0.0)
    breaker.record_failure(key=4)
    assert breaker.is_open(4) is False


def test_declared_user_agent_matches_the_impersonated_tls_version():
    """
    A Chrome 136 TLS fingerprint paired with a different UA string is itself a
    detection signal, so the pool must stay in sync with the impersonated build.
    """
    for _ in range(25):
        headers = random_headers()
        assert "136" in headers["User-Agent"]
        assert "136" in headers["Sec-CH-UA"]


def test_x_requested_with_is_never_sent():
    """Real Chrome fetch() does not send it; its presence flags a bot."""
    for _ in range(25):
        assert "X-Requested-With" not in random_headers()


def test_base_headers_are_not_mutated_between_calls():
    base = {"Accept": "application/json"}
    random_headers(base)
    assert base == {"Accept": "application/json"}
