"""Prometheus-style metrics for the API gateway.

Works with or without the ``prometheus_client`` library installed. When the
library is present, real counters/histograms/gauges are registered in a local
``CollectorRegistry`` and ``generate_latest`` is used for exposition. Otherwise
pure in-memory data structures are used and Prometheus text is generated
manually.
"""
from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Iterable
from threading import Lock
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

try:  # pragma: no cover
    import prometheus_client as _pc
    from prometheus_client import (
        CollectorRegistry,
    )
    from prometheus_client import (
        Counter as _PCCounter,
    )
    from prometheus_client import (
        Gauge as _PCGauge,
    )
    from prometheus_client import (
        Histogram as _PCHistogram,
    )

    _HAS_PC = True
except ImportError:  # pragma: no cover
    _pc = None  # type: ignore[assignment]
    _PCCounter = _PCGauge = _PCHistogram = CollectorRegistry = None  # type: ignore[misc,assignment]
    _HAS_PC = False


REGISTRY: Any = None
if _HAS_PC:
    REGISTRY = CollectorRegistry()


_B_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


def _escape_label(value: str) -> str:
    """Minimal Prometheus text escaping for label values."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_sample(name: str, labels: dict[str, str], value: float) -> str:
    if labels:
        pairs = ", ".join(
            f'{k}="{_escape_label(v)}"' for k, v in sorted(labels.items())
        )
        return f"{name}{{{pairs}}} {value}"
    return f"{name} {value}"


class _Metric:
    def __init__(self, name: str, description: str, labelnames: Iterable[str]) -> None:
        self.name = name
        self.description = description
        self.labelnames = tuple(str(label) for label in labelnames)
        _METRICS.append(self)

    def _collect(self) -> list[str]:
        raise NotImplementedError

    def _label_dict(self, **labels: str) -> dict[str, str]:
        return {k: str(labels.get(k, "")) for k in self.labelnames}


class Counter(_Metric):
    """Counter that increments."""

    def __init__(self, name: str, description: str, labelnames: Iterable[str] = ()) -> None:
        super().__init__(name, description, labelnames)
        if _HAS_PC:
            self._metric: Any = _PCCounter(name, description, labelnames=self.labelnames, registry=REGISTRY)
            self._lock: Lock | None = None
        else:
            self._metric = None
            self._values: dict[tuple[str, ...], float] = defaultdict(float)
            self._lock = Lock()

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if _HAS_PC:
            if self.labelnames:
                self._metric.labels(**self._label_dict(**labels)).inc(amount)
            else:
                self._metric.inc(amount)
            return
        key = tuple(self._label_dict(**labels).values())
        with self._lock:  # type: ignore[union-attr]
            self._values[key] += amount

    def _collect(self) -> list[str]:
        if _HAS_PC:
            return []
        lines = [f"# HELP {self.name} {self.description}", f"# TYPE {self.name} counter"]
        for key in sorted(self._values):
            labels = dict(zip(self.labelnames, key, strict=True))
            lines.append(_format_sample(self.name, labels, self._values[key]))
        return lines


class Histogram(_Metric):
    """Histogram that observes durations."""

    def __init__(
        self,
        name: str,
        description: str,
        labelnames: Iterable[str] = (),
        buckets: Iterable[float] = _B_BUCKETS,
    ) -> None:
        super().__init__(name, description, labelnames)
        self._buckets = tuple(sorted(buckets)) + (float("inf"),)
        if _HAS_PC:
            self._metric: Any = _PCHistogram(
                name,
                description,
                labelnames=self.labelnames,
                buckets=self._buckets[:-1],  # prometheus_client adds +Inf for us
                registry=REGISTRY,
            )
            self._lock: Lock | None = None
        else:
            self._metric = None
            self._counts: dict[tuple[str, ...], list[int]] = defaultdict(lambda: [0] * len(self._buckets))
            self._sums: dict[tuple[str, ...], float] = defaultdict(float)
            self._totals: dict[tuple[str, ...], int] = defaultdict(int)
            self._lock = Lock()

    def observe(self, amount: float, **labels: str) -> None:
        if _HAS_PC:
            if self.labelnames:
                self._metric.labels(**self._label_dict(**labels)).observe(amount)
            else:
                self._metric.observe(amount)
            return
        key = tuple(self._label_dict(**labels).values())
        with self._lock:  # type: ignore[union-attr]
            counts = self._counts[key]
            for i, boundary in enumerate(self._buckets):
                if amount <= boundary:
                    counts[i] += 1
                    break
            self._sums[key] += amount
            self._totals[key] += 1

    def _collect(self) -> list[str]:
        if _HAS_PC:
            return []
        lines = [f"# HELP {self.name} {self.description}", f"# TYPE {self.name} histogram"]
        for key in sorted(self._counts):
            labels = dict(zip(self.labelnames, key, strict=True))
            counts = self._counts[key]
            for boundary, count in zip(self._buckets, counts, strict=True):
                le = "+Inf" if boundary == float("inf") else str(boundary)
                bucket_labels = {**labels, "le": le}
                lines.append(_format_sample(f"{self.name}_bucket", bucket_labels, count))
            if key in self._sums:
                lines.append(_format_sample(f"{self.name}_sum", labels, self._sums[key]))
                lines.append(_format_sample(f"{self.name}_count", labels, self._totals[key]))
        return lines


class Gauge(_Metric):
    """Gauge that can be set, incremented or decremented."""

    def __init__(self, name: str, description: str, labelnames: Iterable[str] = ()) -> None:
        super().__init__(name, description, labelnames)
        if _HAS_PC:
            self._metric: Any = _PCGauge(name, description, labelnames=self.labelnames, registry=REGISTRY)
            self._lock: Lock | None = None
        else:
            self._metric = None
            self._values: dict[tuple[str, ...], float] = defaultdict(float)
            self._lock = Lock()

    def set(self, value: float, **labels: str) -> None:
        if _HAS_PC:
            if self.labelnames:
                self._metric.labels(**self._label_dict(**labels)).set(value)
            else:
                self._metric.set(value)
            return
        key = tuple(self._label_dict(**labels).values())
        with self._lock:  # type: ignore[union-attr]
            self._values[key] = value

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if _HAS_PC:
            if self.labelnames:
                self._metric.labels(**self._label_dict(**labels)).inc(amount)
            else:
                self._metric.inc(amount)
            return
        key = tuple(self._label_dict(**labels).values())
        with self._lock:  # type: ignore[union-attr]
            self._values[key] += amount

    def dec(self, amount: float = 1.0, **labels: str) -> None:
        if _HAS_PC:
            if self.labelnames:
                self._metric.labels(**self._label_dict(**labels)).dec(amount)
            else:
                self._metric.dec(amount)
            return
        key = tuple(self._label_dict(**labels).values())
        with self._lock:  # type: ignore[union-attr]
            self._values[key] -= amount

    def _collect(self) -> list[str]:
        if _HAS_PC:
            return []
        lines = [f"# HELP {self.name} {self.description}", f"# TYPE {self.name} gauge"]
        for key in sorted(self._values):
            labels = dict(zip(self.labelnames, key, strict=True))
            lines.append(_format_sample(self.name, labels, self._values[key]))
        return lines


_METRICS: list[_Metric] = []

http_requests_total = Counter("http_requests_total", "Total HTTP requests", ["method", "status"])
http_request_duration_seconds = Histogram("http_request_duration_seconds", "HTTP request duration", ["method"])
http_requests_in_flight = Gauge("http_requests_in_flight", "In-flight HTTP requests")
cross_agent_calls_total = Counter("cross_agent_calls_total", "Cross-agent client calls", ["client", "status"])


def generate_metrics() -> str:
    """Return Prometheus exposition text."""
    if _HAS_PC:
        return _pc.generate_latest(REGISTRY).decode("utf-8")
    lines: list[str] = []
    for metric in _METRICS:
        lines.extend(metric._collect())
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


class MetricsMiddleware:
    """ASGI middleware that records request counts, durations, and in-flight."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "UNKNOWN")
        start = time.perf_counter()
        http_requests_in_flight.inc()
        status_code = 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            if not status_code:
                status_code = 500
            raise
        finally:
            http_requests_in_flight.dec()
            recorded = status_code or 500
            http_requests_total.inc(method=method, status=str(recorded))
            http_request_duration_seconds.observe(time.perf_counter() - start, method=method)
