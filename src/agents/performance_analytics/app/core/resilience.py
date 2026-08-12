"""Resilient HTTP client with retry and circuit breaker.

Provides a synchronous ``ResilientClient`` that wraps ``httpx.Client``. It
retries transient failures (5xx, timeout, network errors) with exponential
backoff and tracks consecutive failures in a circuit breaker.
"""

from __future__ import annotations

import logging
import time
from enum import StrEnum
from threading import Lock
from typing import Any

import httpx

from app.core import metrics

logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    closed = "closed"
    open = "open"
    half_open = "half_open"


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open and a call is rejected."""


class CircuitBreaker:
    """Simple thread-safe circuit breaker."""

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = CircuitState.closed
        self._failures = 0
        self._last_failure: float | None = None
        self._probe_in_progress = False
        self._lock = Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    def allow(self) -> bool:
        with self._lock:
            if self._state == CircuitState.closed:
                return True

            if self._state == CircuitState.open:
                if self._last_failure is None:
                    return False
                if (time.monotonic() - self._last_failure) >= self.recovery_timeout:
                    self._state = CircuitState.half_open
                    self._probe_in_progress = False
                    return True
                return False

            if self._state == CircuitState.half_open:
                if self._probe_in_progress:
                    return False
                self._probe_in_progress = True
                return True

            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._last_failure = None
            self._probe_in_progress = False
            self._state = CircuitState.closed

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._last_failure = time.monotonic()
            self._probe_in_progress = False
            if self._failures >= self.failure_threshold:
                self._state = CircuitState.open

    def reset(self) -> None:
        with self._lock:
            self._state = CircuitState.closed
            self._failures = 0
            self._last_failure = None
            self._probe_in_progress = False


class ResilientClient:
    """Synchronous resilient HTTP client with circuit breaker and retries."""

    def __init__(
        self,
        base_url: str | None = None,
        client: httpx.Client | None = None,
        max_retries: int = 3,
        base_delay: float = 0.2,
        circuit_breaker: CircuitBreaker | None = None,
        client_name: str = "unknown",
    ) -> None:
        self.base_url = base_url
        self._client = client
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.circuit = circuit_breaker or CircuitBreaker()
        self.client_name = client_name

    def _new_client(self, timeout: float | None = None) -> httpx.Client:
        if self._client is not None:
            return self._client
        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        return httpx.Client(**kwargs)

    def _is_retryable(self, exc: Exception) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code >= 500
        return isinstance(exc, (httpx.RequestError,))

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """POST with retries and circuit breaker."""
        metrics.cross_agent_calls_total.inc({"client": self.client_name})

        if not self.circuit.allow():
            raise CircuitOpenError(
                f"Circuit breaker is open for {self.client_name}; rejecting call to {url}"
            )

        full_url = url
        if self.base_url:
            full_url = self.base_url.rstrip("/") + "/" + url.lstrip("/")

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                with self._new_client(timeout=timeout) as client:
                    response = client.post(full_url, json=json, headers=headers)
                    response.raise_for_status()
                    self.circuit.record_success()
                    return response.json()
            except httpx.HTTPStatusError as exc:
                if self._is_retryable(exc):
                    last_exc = exc
                    self.circuit.record_failure()
                    if attempt < self.max_retries - 1:
                        time.sleep(self.base_delay * (2**attempt))
                        continue
                    raise
                self.circuit.record_failure()
                raise
            except httpx.RequestError as exc:
                last_exc = exc
                self.circuit.record_failure()
                if attempt < self.max_retries - 1:
                    time.sleep(self.base_delay * (2**attempt))
                    continue
                raise
            except Exception:
                self.circuit.record_failure()
                raise

        if last_exc is not None:
            raise last_exc
        raise CircuitOpenError(f"Exhausted retries for {full_url}")

    def reset(self) -> None:
        """Reset the circuit breaker."""
        self.circuit.reset()
