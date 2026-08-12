"""Cross-agent HTTP resilience: circuit breaker + retry with exponential backoff.

``ResilientClient`` wraps ``httpx.Client`` (sync). It retries 5xx / network /
timeout errors up to three times with a 0.2s base exponential backoff, tracks
per-client circuit-breaker state, and emits ``cross_agent_calls_total``.
"""
from __future__ import annotations

import time
import uuid
from threading import RLock
from typing import Any

import httpx
import structlog

from app.core import metrics


class CircuitBreakerOpen(Exception):
    """Raised when the circuit breaker is open and the caller should fall back."""


class CircuitBreaker:
    """Simple half-open circuit breaker with a fixed failure threshold and timeout."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, name: str, failure_threshold: int = 3, timeout: float = 30.0) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self._lock = RLock()
        self._state = self.CLOSED
        self._consecutive_failures = 0
        self._last_opened = 0.0
        self._half_open_probe = False

    def is_open(self) -> bool:
        with self._lock:
            if self._state == self.OPEN:
                if time.monotonic() - self._last_opened >= self.timeout:
                    self._state = self.HALF_OPEN
                    self._half_open_probe = False
                    return False
                return True
            if self._state == self.HALF_OPEN:
                if self._half_open_probe:
                    return True
                self._half_open_probe = True
                return False
            return False

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            if self._state == self.HALF_OPEN:
                self._state = self.CLOSED
                self._half_open_probe = False

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                self._state = self.OPEN
                self._last_opened = time.monotonic()
                self._half_open_probe = False


_circuits: dict[str, CircuitBreaker] = {}
_circuit_lock = RLock()


def get_circuit(client: str) -> CircuitBreaker:
    with _circuit_lock:
        if client not in _circuits:
            _circuits[client] = CircuitBreaker(client)
        return _circuits[client]


def reset_circuits() -> None:
    """Reset all circuit breakers (test helper)."""
    with _circuit_lock:
        _circuits.clear()


def reset_circuit(client: str) -> None:
    """Reset the circuit breaker for a single client (test helper)."""
    with _circuit_lock:
        _circuits.pop(client, None)


def _request_id() -> str:
    ctx = structlog.contextvars.get_contextvars()
    return str(ctx.get("request_id") or uuid.uuid4().hex)


def _record_call(client: str, response: httpx.Response | None = None, error: str = "") -> None:
    status = str(response.status_code) if response is not None else (error or "error")
    metrics.CROSS_AGENT_CALLS_TOTAL.labels(client=client, status=status).inc()


class ResilientClient:
    """Sync HTTP client with retry and a circuit breaker.

    Keeps the same high-level ``post`` / ``request`` surface as ``httpx.Client``
    so callers only need to swap the client instantiation.
    """

    def __init__(
        self,
        client: str = "unknown",
        max_attempts: int = 3,
        base_delay: float = 0.2,
        max_delay: float = 30.0,
        timeout: float = 5.0,
        circuit: CircuitBreaker | None = None,
    ) -> None:
        self.client = client
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.timeout = timeout
        self.circuit = circuit or get_circuit(client)

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        **kwargs: Any,
    ) -> httpx.Response:
        return self.request("POST", url, headers=headers, json=json, **kwargs)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        if self.circuit.is_open():
            raise CircuitBreakerOpen(f"Circuit breaker open for client={self.client}")

        request_headers = dict(headers or {})
        request_headers.setdefault("X-Request-ID", _request_id())

        last_response: httpx.Response | None = None
        for attempt in range(self.max_attempts):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    http_method = getattr(client, method.lower(), None)
                    if http_method is not None:
                        response = http_method(url, headers=request_headers, **kwargs)
                    else:
                        response = client.request(method, url, headers=request_headers, **kwargs)
                last_response = response
                if response.status_code < 500:
                    self.circuit.record_success()
                    _record_call(self.client, response=response)
                    return response
                # 5xx: record a failure and retry if we have attempts left.
                self.circuit.record_failure()
                _record_call(self.client, response=response)
            except (httpx.NetworkError, httpx.TimeoutException) as exc:
                self.circuit.record_failure()
                _record_call(self.client, error=type(exc).__name__)
                if attempt == self.max_attempts - 1:
                    raise
            except httpx.HTTPError as exc:
                # Other httpx errors are not obviously transient; do not retry.
                self.circuit.record_failure()
                _record_call(self.client, error=type(exc).__name__)
                raise
            except Exception as exc:  # noqa: BLE001
                self.circuit.record_failure()
                _record_call(self.client, error=type(exc).__name__)
                raise

            if attempt < self.max_attempts - 1:
                sleep = min(self.base_delay * (2**attempt), self.max_delay)
                time.sleep(sleep)

        # Exhausted retries on 5xx; return the final response and let the caller decide.
        if last_response is not None:
            return last_response
        raise RuntimeError("ResilientClient exhausted retries without a response")
