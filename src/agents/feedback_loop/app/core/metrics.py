"""Prometheus-compatible metrics with an in-memory fallback.

If ``prometheus_client`` is installed the module delegates to the real library
and exposes the default ``REGISTRY``. Otherwise it keeps counters, histograms
and gauges in plain Python data structures and emits Prometheus text exposition
format manually.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import PlainTextResponse, Response

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        generate_latest,
    )
    from prometheus_client import (
        REGISTRY as _PROM_REGISTRY,
    )
    from prometheus_client import (
        Counter as _PromCounter,
    )
    from prometheus_client import (
        Gauge as _PromGauge,
    )
    from prometheus_client import (
        Histogram as _PromHistogram,
    )

    _PROMETHEUS = True
except Exception:  # pragma: no cover - optional dependency
    _PROMETHEUS = False
    _PROM_REGISTRY = None
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"


class _InMemoryRegistry:
    """Simple collector for in-memory metric wrappers."""

    def __init__(self) -> None:
        self._metrics: list[Any] = []
        self._lock = threading.Lock()

    def register(self, metric: Any) -> None:
        with self._lock:
            self._metrics.append(metric)

    def collect(self) -> list[Any]:
        with self._lock:
            return list(self._metrics)


if _PROMETHEUS:
    REGISTRY = _PROM_REGISTRY
else:
    REGISTRY = _InMemoryRegistry()


def _format_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    parts = [f'{k}="{v}"' for k, v in labels.items()]
    return "{" + ",".join(parts) + "}"


class _InMemoryCounter:
    def __init__(self, name: str, documentation: str, labelnames: tuple[str, ...]) -> None:
        self.name = name
        self.documentation = documentation
        self.labelnames = labelnames
        self._values: dict[tuple[str, ...], float] = defaultdict(float)
        self._lock = threading.Lock()

    def inc(self, labels: dict[str, str] | None = None, amount: float = 1) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] += amount

    def _key(self, labels: dict[str, str] | None) -> tuple[str, ...]:
        if not self.labelnames or labels is None:
            return ()
        return tuple(str(labels.get(k, "")) for k in self.labelnames)

    def collect(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.documentation}", f"# TYPE {self.name} counter"]
        with self._lock:
            for key, value in sorted(self._values.items()):
                label_dict = {k: v for k, v in zip(self.labelnames, key)} if self.labelnames else {}
                lines.append(f"{self.name}{_format_labels(label_dict)} {value}")
        return lines


class _InMemoryHistogram:
    _buckets = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf"))

    def __init__(self, name: str, documentation: str, labelnames: tuple[str, ...]) -> None:
        self.name = name
        self.documentation = documentation
        self.labelnames = labelnames
        self._values: dict[tuple[str, ...], list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def observe(self, labels: dict[str, str] | None, amount: float) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key].append(amount)

    def _key(self, labels: dict[str, str] | None) -> tuple[str, ...]:
        if not self.labelnames or labels is None:
            return ()
        return tuple(str(labels.get(k, "")) for k in self.labelnames)

    @staticmethod
    def _le_labels(labels: dict[str, str], le: float) -> dict[str, str]:
        out = dict(labels)
        out["le"] = str(le) if le != float("inf") else "+Inf"
        return out

    def collect(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.documentation}", f"# TYPE {self.name} histogram"]
        with self._lock:
            for key, observations in sorted(self._values.items()):
                label_dict = {k: v for k, v in zip(self.labelnames, key)} if self.labelnames else {}
                for bucket in self._buckets:
                    count = sum(1 for o in observations if o <= bucket)
                    lines.append(
                        f"{self.name}_bucket{_format_labels(self._le_labels(label_dict, bucket))} {count}"
                    )
                total = sum(observations)
                lines.append(f"{self.name}_sum{_format_labels(label_dict)} {total}")
                lines.append(f"{self.name}_count{_format_labels(label_dict)} {len(observations)}")
        return lines


class _InMemoryGauge:
    def __init__(self, name: str, documentation: str, labelnames: tuple[str, ...]) -> None:
        self.name = name
        self.documentation = documentation
        self.labelnames = labelnames
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def set(self, labels: dict[str, str] | None, value: float) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = value

    def inc(self, labels: dict[str, str] | None = None, amount: float = 1) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0) + amount

    def dec(self, labels: dict[str, str] | None = None, amount: float = 1) -> None:
        self.inc(labels, -amount)

    def _key(self, labels: dict[str, str] | None) -> tuple[str, ...]:
        if not self.labelnames or labels is None:
            return ()
        return tuple(str(labels.get(k, "")) for k in self.labelnames)

    def collect(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.documentation}", f"# TYPE {self.name} gauge"]
        with self._lock:
            for key, value in sorted(self._values.items()):
                label_dict = {k: v for k, v in zip(self.labelnames, key)} if self.labelnames else {}
                lines.append(f"{self.name}{_format_labels(label_dict)} {value}")
        return lines


class Counter:
    """Prometheus-style counter that increments and never resets."""

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: tuple[str, ...] | list[str] = (),
        registry: Any | None = REGISTRY,
    ) -> None:
        self.name = name
        self._labelnames = tuple(labelnames)
        self._real: Any | None = None
        self._inner: _InMemoryCounter | None = None
        if _PROMETHEUS and registry is not None:
            self._real = _PromCounter(name, documentation, labelnames=self._labelnames, registry=registry)
        else:
            self._inner = _InMemoryCounter(name, documentation, self._labelnames)
            REGISTRY.register(self)

    def _label_kwargs(self, labels: dict[str, str] | None) -> dict[str, str]:
        if not labels or not self._labelnames:
            return {}
        return {k: labels.get(k, "") for k in self._labelnames}

    def inc(self, labels: dict[str, str] | None = None, amount: float = 1) -> None:
        if self._real is not None:
            if self._labelnames:
                self._real.labels(**self._label_kwargs(labels)).inc(amount)
            else:
                self._real.inc(amount)
        elif self._inner is not None:
            self._inner.inc(labels, amount)

    def collect(self) -> list[str]:
        if self._inner is not None:
            return self._inner.collect()
        return []


class Histogram:
    """Prometheus-style histogram for request durations."""

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: tuple[str, ...] | list[str] = (),
        registry: Any | None = REGISTRY,
        buckets: tuple[float, ...] | None = None,
    ) -> None:
        self.name = name
        self._labelnames = tuple(labelnames)
        self._real: Any | None = None
        self._inner: _InMemoryHistogram | None = None
        if _PROMETHEUS and registry is not None:
            kwargs: dict[str, Any] = {"labelnames": self._labelnames, "registry": registry}
            if buckets is not None:
                kwargs["buckets"] = buckets
            self._real = _PromHistogram(name, documentation, **kwargs)
        else:
            self._inner = _InMemoryHistogram(name, documentation, self._labelnames)
            if buckets is not None:
                self._inner._buckets = tuple(buckets)
            REGISTRY.register(self)

    def _label_kwargs(self, labels: dict[str, str] | None) -> dict[str, str]:
        if not labels or not self._labelnames:
            return {}
        return {k: labels.get(k, "") for k in self._labelnames}

    def observe(self, labels: dict[str, str] | None, amount: float) -> None:
        if self._real is not None:
            if self._labelnames:
                self._real.labels(**self._label_kwargs(labels)).observe(amount)
            else:
                self._real.observe(amount)
        elif self._inner is not None:
            self._inner.observe(labels, amount)

    def collect(self) -> list[str]:
        if self._inner is not None:
            return self._inner.collect()
        return []


class Gauge:
    """Prometheus-style gauge that can go up and down."""

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: tuple[str, ...] | list[str] = (),
        registry: Any | None = REGISTRY,
    ) -> None:
        self.name = name
        self._labelnames = tuple(labelnames)
        self._real: Any | None = None
        self._inner: _InMemoryGauge | None = None
        if _PROMETHEUS and registry is not None:
            self._real = _PromGauge(name, documentation, labelnames=self._labelnames, registry=registry)
        else:
            self._inner = _InMemoryGauge(name, documentation, self._labelnames)
            REGISTRY.register(self)

    def _label_kwargs(self, labels: dict[str, str] | None) -> dict[str, str]:
        if not labels or not self._labelnames:
            return {}
        return {k: labels.get(k, "") for k in self._labelnames}

    def set(self, value: float, labels: dict[str, str] | None = None) -> None:
        if self._real is not None:
            if self._labelnames:
                self._real.labels(**self._label_kwargs(labels)).set(value)
            else:
                self._real.set(value)
        elif self._inner is not None:
            self._inner.set(labels, value)

    def inc(self, labels: dict[str, str] | None = None, amount: float = 1) -> None:
        if self._real is not None:
            if self._labelnames:
                self._real.labels(**self._label_kwargs(labels)).inc(amount)
            else:
                self._real.inc(amount)
        elif self._inner is not None:
            self._inner.inc(labels, amount)

    def dec(self, labels: dict[str, str] | None = None, amount: float = 1) -> None:
        if self._real is not None:
            if self._labelnames:
                self._real.labels(**self._label_kwargs(labels)).dec(amount)
            else:
                self._real.dec(amount)
        elif self._inner is not None:
            self._inner.dec(labels, amount)

    def collect(self) -> list[str]:
        if self._inner is not None:
            return self._inner.collect()
        return []


# HTTP middleware metrics
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests",
    labelnames=("method", "status"),
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    labelnames=("method",),
)
HTTP_REQUESTS_IN_FLIGHT = Gauge(
    "http_requests_in_flight",
    "Current in-flight HTTP requests",
)

# Application-specific metrics
KAFKA_PRODUCED_TOTAL = Counter(
    "kafka_produced_total",
    "Total Kafka messages produced",
    labelnames=("topic",),
)
KAFKA_CONSUMED_TOTAL = Counter(
    "kafka_consumed_total",
    "Total Kafka messages consumed",
    labelnames=("topic",),
)
CROSS_AGENT_CALLS_TOTAL = Counter(
    "cross_agent_calls_total",
    "Total cross-agent HTTP calls",
    labelnames=("client", "status"),
)


class MetricMiddleware(BaseHTTPMiddleware):
    """Record per-request Prometheus-style HTTP metrics."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        HTTP_REQUESTS_IN_FLIGHT.inc()
        start = time.perf_counter()
        status = "500"
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        except Exception:
            status = "500"
            raise
        finally:
            duration = time.perf_counter() - start
            HTTP_REQUESTS_TOTAL.inc({"method": request.method, "status": status})
            HTTP_REQUEST_DURATION_SECONDS.observe({"method": request.method}, duration)
            HTTP_REQUESTS_IN_FLIGHT.dec()


def generate_metrics() -> str:
    """Return Prometheus exposition text for all registered metrics."""
    if _PROMETHEUS:
        return generate_latest(REGISTRY).decode("utf-8")

    lines: list[str] = []
    for metric in REGISTRY.collect():
        lines.extend(metric.collect())
    return "\n".join(lines) + "\n"


def metrics_response() -> PlainTextResponse:
    """Convenience helper used by the metrics endpoint."""
    return PlainTextResponse(generate_metrics(), media_type=CONTENT_TYPE_LATEST)
