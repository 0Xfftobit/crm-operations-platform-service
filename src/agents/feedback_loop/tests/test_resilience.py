"""Circuit-breaker and resilient client unit tests."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.core.resilience import CircuitBreaker, CircuitBreakerOpen, ResilientClient


def _run(coro):
    return asyncio.run(coro)


def _failing_handler(request: httpx.Request) -> None:
    raise httpx.ConnectError("connection refused", request=request)


def test_circuit_opens_after_threshold_and_uses_fallback() -> None:
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=60.0)

    fallback_called = False

    def sync_fallback(**_: object) -> str:
        nonlocal fallback_called
        fallback_called = True
        return "fallback"

    client = ResilientClient(
        "http://example.com",
        circuit_breaker=cb,
        fallback=sync_fallback,
        max_retries=1,
        timeout=0.1,
        transport=httpx.MockTransport(_failing_handler),
    )

    # First two calls fail.
    for _ in range(2):
        with pytest.raises((httpx.RequestError, RuntimeError)):
            _run(client.request("GET", "/"))

    # Third call should hit the open circuit and use the fallback.
    result = _run(client.request("GET", "/"))
    assert result == "fallback"
    assert cb.state == cb.OPEN
    _run(client.close())


def test_circuit_half_open_probe_succeeds() -> None:
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.0)

    async def ok() -> str:
        return "ok"

    async def fail() -> None:
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError):
        _run(cb.call(fail))
    assert cb.state == cb.OPEN

    # After a 0s recovery timeout the next call is a half-open probe and succeeds.
    _run(asyncio.sleep(0.01))
    result = _run(cb.call(ok))
    assert result == "ok"
    assert cb.state == cb.CLOSED


def test_circuit_raises_when_open_and_no_fallback() -> None:
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)

    async def fail() -> None:
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError):
        _run(cb.call(fail))

    with pytest.raises(CircuitBreakerOpen):
        _run(cb.call(lambda: "never called"))
