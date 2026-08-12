"""Resilient cross-agent HTTP client with retry and circuit breaker.

Wraps ``httpx.AsyncClient`` with simple exponential-backoff retries for 5xx / timeout /
network errors and a circuit breaker that opens after 3 consecutive failures, stays open
for 30s, then enters half-open and allows a single probe.

X-Request-ID is propagated from structlog contextvars when present; callers may supply
additional default headers (e.g. ``X-API-Key``).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog
from structlog.contextvars import get_contextvars

from app.core import metrics

logger = structlog.get_logger(__name__)


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open."""


class CircuitBreaker:
    """Simple async circuit breaker: closed → open → half-open → closed."""

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = "closed"
        self._failures = 0
        self._last_failure = 0.0
        self._lock = asyncio.Lock()

    async def _can_execute(self) -> bool:
        async with self._lock:
            if self._state == "open":
                if time.monotonic() - self._last_failure >= self.recovery_timeout:
                    self._state = "half-open"
                    self._failures = 0
                    return True
                return False
            return True

    async def record_success(self) -> None:
        async with self._lock:
            self._failures = 0
            if self._state == "half-open":
                self._state = "closed"

    async def record_failure(self) -> None:
        async with self._lock:
            self._failures += 1
            self._last_failure = time.monotonic()
            if self._failures >= self.failure_threshold:
                self._state = "open"


class ResilientClient:
    """Async HTTP client with retries and circuit breaker.

    Exposes ``get``/``post``/``put``/``patch``/``delete`` and a generic ``request``
    method that all share the same resilience logic.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        name: str = "cross_agent",
        max_retries: int = 3,
        base_delay: float = 0.2,
        max_delay: float = 5.0,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout))
        self._circuit = CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
        )
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._default_headers = dict(headers or {})

    def _merge_headers(self, headers: dict[str, str] | None) -> dict[str, str]:
        merged = dict(self._default_headers)
        merged.update(headers or {})
        try:
            request_id = get_contextvars().get("request_id")
        except Exception:  # pragma: no cover
            request_id = None
        if request_id:
            merged.setdefault("x-request-id", str(request_id))
        return merged

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code >= 500
        return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))

    async def request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        if not await self._circuit._can_execute():
            metrics.CROSS_AGENT_CALLS_TOTAL.inc(
                {"client": self.name, "status": "circuit_open"}, 1
            )
            raise CircuitOpenError(f"Circuit open for client '{self.name}'")

        headers = self._merge_headers(kwargs.pop("headers", None))
        last_exc: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                resp = await self._client.request(
                    method, url, headers=headers, **kwargs
                )
                resp.raise_for_status()
                await self._circuit.record_success()
                metrics.CROSS_AGENT_CALLS_TOTAL.inc(
                    {"client": self.name, "status": "success"}, 1
                )
                return resp
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500:
                    metrics.CROSS_AGENT_CALLS_TOTAL.inc(
                        {"client": self.name, "status": "retry"}, 1
                    )
                    last_exc = exc
                    if attempt < self._max_retries - 1:
                        delay = min(
                            self._base_delay * (2 ** attempt), self._max_delay
                        )
                        await asyncio.sleep(delay)
                        continue
                    break
                await self._circuit.record_success()
                metrics.CROSS_AGENT_CALLS_TOTAL.inc(
                    {"client": self.name, "status": "client_error"}, 1
                )
                raise
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                metrics.CROSS_AGENT_CALLS_TOTAL.inc(
                    {"client": self.name, "status": "retry"}, 1
                )
                last_exc = exc
                if attempt < self._max_retries - 1:
                    delay = min(self._base_delay * (2 ** attempt), self._max_delay)
                    await asyncio.sleep(delay)
                    continue
                break

        await self._circuit.record_failure()
        metrics.CROSS_AGENT_CALLS_TOTAL.inc(
            {"client": self.name, "status": "error"}, 1
        )
        if last_exc is None:
            raise CircuitOpenError(f"Circuit open for client '{self.name}'")
        raise last_exc

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("DELETE", url, **kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> ResilientClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()
