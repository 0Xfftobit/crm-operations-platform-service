"""Resilient HTTP client with retry and circuit breaker."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

import httpx
import structlog

from app.core import metrics as core_metrics

logger = logging.getLogger(__name__)

_INSTANCES: list[ResilientClient] = []
_INSTANCES_LOCK = threading.Lock()


class CircuitBreaker:
    """Simple circuit breaker: opens after ``N`` consecutive failures."""

    def __init__(
        self,
        failure_threshold: int = 3,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self._failures = 0
        self._state = "closed"  # closed, open, half_open
        self._last_failure_time: float | None = None
        self._lock = threading.Lock()

    def can_execute(self) -> bool:
        with self._lock:
            if self._state == "open":
                if self._last_failure_time is not None:
                    elapsed = time.monotonic() - self._last_failure_time
                    if elapsed > self.timeout_seconds:
                        self._state = "half_open"
                        return True
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = "closed"

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._last_failure_time = time.monotonic()
            if self._failures >= self.failure_threshold:
                self._state = "open"

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = "closed"
            self._last_failure_time = None


class ResilientClient:
    """Sync HTTP client wrapper with retries and circuit breaker.

    Retries up to ``max_attempts`` for 5xx, timeout and network errors using
    exponential backoff. Falls back to ``fallback`` when the circuit is open or
    all retries are exhausted, so callers never block.
    """

    def __init__(
        self,
        *,
        client_label: str = "",
        timeout: float | None = None,
        max_attempts: int = 3,
        retry_base_delay: float = 0.2,
    ) -> None:
        self.client_label = client_label
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.retry_base_delay = retry_base_delay
        self._circuit = CircuitBreaker()
        with _INSTANCES_LOCK:
            _INSTANCES.append(self)

    def _is_retryable(self, exc: BaseException) -> bool:
        return isinstance(
            exc,
            (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
            ),
        )

    def _status_from_error(self, exc: BaseException) -> str:
        if isinstance(exc, httpx.TimeoutException):
            return "timeout"
        return "network_error"

    def _ensure_request_id(self, headers: dict[str, str] | None) -> dict[str, str]:
        headers = dict(headers or {})
        if "X-Request-ID" not in headers:
            try:
                ctx = structlog.contextvars.get_contextvars()
                request_id = ctx.get("request_id")
                if request_id:
                    headers["X-Request-ID"] = request_id
            except Exception:
                pass
        return headers

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any | None = None,
        timeout: float | None = None,
        fallback: Any | None = None,
        client_label: str | None = None,
    ) -> Any:
        label = client_label or self.client_label
        if not self._circuit.can_execute():
            core_metrics.inc_cross_agent_calls(client=label, status="circuit_open")
            return fallback

        headers = self._ensure_request_id(headers)
        timeout = timeout if timeout is not None else self.timeout

        for attempt in range(self.max_attempts):
            try:
                with httpx.Client(timeout=timeout) as client:
                    request_method = getattr(client, method.lower(), client.request)
                    request_kwargs: dict[str, Any] = {"headers": headers}
                    if json is not None:
                        request_kwargs["json"] = json
                    response = request_method(url, **request_kwargs)
                    if response.is_success:
                        self._circuit.record_success()
                        core_metrics.inc_cross_agent_calls(client=label, status="success")
                        return response.json()
                    if response.status_code >= 500:
                        core_metrics.inc_cross_agent_calls(client=label, status="server_error")
                        self._circuit.record_failure()
                        if attempt < self.max_attempts - 1:
                            time.sleep(self.retry_base_delay * (2**attempt))
                        continue
                    # 4xx / other HTTP errors are not retryable
                    self._circuit.record_success()
                    core_metrics.inc_cross_agent_calls(client=label, status="client_error")
                    return fallback
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                core_metrics.inc_cross_agent_calls(client=label, status=self._status_from_error(exc))
                self._circuit.record_failure()
                if attempt < self.max_attempts - 1:
                    time.sleep(self.retry_base_delay * (2**attempt))
            except Exception:
                core_metrics.inc_cross_agent_calls(client=label, status="error")
                self._circuit.record_failure()
                if attempt < self.max_attempts - 1:
                    time.sleep(self.retry_base_delay * (2**attempt))

        return fallback

    def reset(self) -> None:
        self._circuit.reset()


class AsyncResilientClient:
    """Async version of :class:`ResilientClient`."""

    def __init__(
        self,
        *,
        client_label: str = "",
        timeout: float | None = None,
        max_attempts: int = 3,
        retry_base_delay: float = 0.2,
    ) -> None:
        self.client_label = client_label
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.retry_base_delay = retry_base_delay
        self._circuit = CircuitBreaker()
        with _INSTANCES_LOCK:
            _INSTANCES.append(self)

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any | None = None,
        timeout: float | None = None,
        fallback: Any | None = None,
        client_label: str | None = None,
    ) -> Any:
        label = client_label or self.client_label
        if not self._circuit.can_execute():
            core_metrics.inc_cross_agent_calls(client=label, status="circuit_open")
            return fallback

        headers = dict(headers or {})
        if "X-Request-ID" not in headers:
            try:
                ctx = structlog.contextvars.get_contextvars()
                request_id = ctx.get("request_id")
                if request_id:
                    headers["X-Request-ID"] = request_id
            except Exception:
                pass

        timeout = timeout if timeout is not None else self.timeout

        for attempt in range(self.max_attempts):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    request_method = getattr(client, method.lower(), client.request)
                    request_kwargs: dict[str, Any] = {"headers": headers}
                    if json is not None:
                        request_kwargs["json"] = json
                    response = await request_method(url, **request_kwargs)
                    if response.is_success:
                        self._circuit.record_success()
                        core_metrics.inc_cross_agent_calls(client=label, status="success")
                        return response.json()
                    if response.status_code >= 500:
                        core_metrics.inc_cross_agent_calls(client=label, status="server_error")
                        self._circuit.record_failure()
                        if attempt < self.max_attempts - 1:
                            await asyncio.sleep(self.retry_base_delay * (2**attempt))
                        continue
                    self._circuit.record_success()
                    core_metrics.inc_cross_agent_calls(client=label, status="client_error")
                    return fallback
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                core_metrics.inc_cross_agent_calls(client=label, status="timeout" if isinstance(exc, httpx.TimeoutException) else "network_error")
                self._circuit.record_failure()
                if attempt < self.max_attempts - 1:
                    await asyncio.sleep(self.retry_base_delay * (2**attempt))
            except Exception:
                core_metrics.inc_cross_agent_calls(client=label, status="error")
                self._circuit.record_failure()
                if attempt < self.max_attempts - 1:
                    await asyncio.sleep(self.retry_base_delay * (2**attempt))

        return fallback

    def reset(self) -> None:
        self._circuit.reset()


def reset_resilience() -> None:
    """Reset all circuit breakers (test helper)."""
    with _INSTANCES_LOCK:
        for instance in _INSTANCES:
            instance.reset()
