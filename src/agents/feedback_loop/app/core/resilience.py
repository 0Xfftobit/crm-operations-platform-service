"""Resilient cross-agent HTTP client.

Wraps ``httpx.AsyncClient`` with a simple retry loop and a circuit breaker.
When the circuit is open a configurable fallback is invoked so callers never
block while waiting for an unavailable downstream agent.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog

from app.core.config import get_settings

logger = structlog.get_logger(__name__)


def _get_request_id() -> str | None:
    """Return the current structlog request_id context if one is bound."""
    try:
        return structlog.contextvars.get_contextvars().get("request_id")
    except Exception:
        return None


class CircuitBreakerOpen(Exception):
    """Raised when the circuit breaker is OPEN and no probe is allowed."""


class CircuitBreaker:
    """Simple circuit breaker with exponential-probe half-open state."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure_time = 0.0
        self.state = self.CLOSED
        self._lock = asyncio.Lock()

    async def call(self, fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
        async with self._lock:
            if self.state == self.OPEN:
                if time.monotonic() - self.last_failure_time >= self.recovery_timeout:
                    self.state = self.HALF_OPEN
                    self.failures = 0
                else:
                    raise CircuitBreakerOpen()

        try:
            result = await fn(*args, **kwargs)
            async with self._lock:
                if self.state == self.HALF_OPEN:
                    self.state = self.CLOSED
                self.failures = 0
            return result
        except Exception as exc:
            async with self._lock:
                self.failures += 1
                self.last_failure_time = time.monotonic()
                if self.failures >= self.failure_threshold:
                    self.state = self.OPEN
            raise exc


class ResilientClient:
    """httpx.AsyncClient wrapper with retries, circuit breaker and fallback."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = 10.0,
        max_retries: int = 3,
        base_backoff: float = 0.2,
        circuit_breaker: CircuitBreaker | None = None,
        fallback: Callable[..., Any] | None = None,
        **client_kwargs: Any,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key or get_settings().api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        self.circuit = circuit_breaker or CircuitBreaker()
        self.fallback = fallback
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, **client_kwargs)

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Any:
        headers = dict(headers or {})
        if "X-API-Key" not in headers and self.api_key:
            headers["X-API-Key"] = self.api_key
        if "X-Request-ID" not in headers:
            request_id = _get_request_id()
            if request_id:
                headers["X-Request-ID"] = request_id

        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

        last_exception: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return await self.circuit.call(self._client.request, method, url, headers=headers, **kwargs)
            except CircuitBreakerOpen:
                if self.fallback:
                    return self.fallback(method=method, path=path, headers=headers, **kwargs)
                raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError) as exc:
                last_exception = exc
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500:
                    last_exception = exc
                else:
                    raise
            if attempt < self.max_retries - 1:
                await asyncio.sleep(self.base_backoff * (2**attempt))

        if last_exception:
            raise last_exception
        raise httpx.RequestError("exhausted retries", request=None)

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self.request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> Any:
        return await self.request("PUT", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> Any:
        return await self.request("PATCH", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> Any:
        return await self.request("DELETE", path, **kwargs)

    async def close(self) -> None:
        await self._client.aclose()
