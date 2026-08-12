"""Prometheus-style metrics with a graceful in-memory fallback.

When ``prometheus_client`` is installed the wrappers delegate to the real library and
``generate_metrics`` returns its exposition text.  Otherwise the wrappers keep simple
in-memory data structures and render Prometheus exposition text manually.  This keeps the
agent observable in broker-free / dependency-free test runs and local development.
"""
from __future__ import annotations

import time
from threading import Lock
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware

try:
    from prometheus_client import CollectorRegistry, generate_latest
    from prometheus_client import Counter as _PCCounter
    from prometheus_client import Gauge as _PCGauge
    from prometheus_client import Histogram as _PCHistogram

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PROMETHEUS_AVAILABLE = False


class Registry:
    """Global metric registry.  Keeps a list of in-memory metrics and an optional
    ``prometheus_client.CollectorRegistry``.
    """

    def __init__(self) -> None:
        self._metrics: list[Metric] = []
        self._pc_registry: Any = None
        if _PROMETHEUS_AVAILABLE:
            self._pc_registry = CollectorRegistry(auto_describe=True)

    def register(self, metric: Metric) -> Metric:
        self._metrics.append(metric)
        return metric


REGISTRY = Registry()


def _label_key(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def _label_key_to_dict(key: tuple[tuple[str, str], ...]) -> dict[str, str]:
    return dict(key)


def _filter_labels(metric: Metric, labels: dict[str, str] | None) -> dict[str, str]:
    if not labels:
        return {}
    return {k: str(v) for k, v in labels.items() if k in metric.labelnames}


class Metric:
    """Base metric wrapper."""

    _type = ""

    def __init__(self, name: str, doc: str, labelnames: tuple[str, ...]) -> None:
        self.name = name
        self.doc = doc
        self.labelnames = labelnames
        self._values: dict[tuple[tuple[str, str], ...], Any] = {}
        self._lock = Lock()
        REGISTRY.register(self)

    def _key(self, labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
        return _label_key(_filter_labels(self, labels))

    def _samples(self) -> list[tuple[dict[str, str], str, float]]:
        raise NotImplementedError


class Counter(Metric):
    """Prometheus-style counter."""

    _type = "counter"

    def __init__(self, name: str, doc: str, labelnames: tuple[str, ...] = ()) -> None:
        super().__init__(name, doc, labelnames)
        if _PROMETHEUS_AVAILABLE:
            self._pc = _PCCounter(name, doc, labelnames, registry=REGISTRY._pc_registry)
        else:
            self._pc = None

    def inc(self, labels: dict[str, str] | None = None, amount: float = 1) -> None:
        amount = float(amount)
        if self._pc is not None:
            if self.labelnames:
                self._pc.labels(**_filter_labels(self, labels)).inc(amount)
            else:
                self._pc.inc(amount)
            return
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def _samples(self) -> list[tuple[dict[str, str], str, float]]:
        with self._lock:
            return [
                (_label_key_to_dict(key), "", float(value))
                for key, value in self._values.items()
            ]


class Gauge(Metric):
    """Prometheus-style gauge."""

    _type = "gauge"

    def __init__(self, name: str, doc: str, labelnames: tuple[str, ...] = ()) -> None:
        super().__init__(name, doc, labelnames)
        if _PROMETHEUS_AVAILABLE:
            self._pc = _PCGauge(name, doc, labelnames, registry=REGISTRY._pc_registry)
        else:
            self._pc = None

    def set(self, labels: dict[str, str] | None = None, value: float = 0) -> None:
        value = float(value)
        if self._pc is not None:
            if self.labelnames:
                self._pc.labels(**_filter_labels(self, labels)).set(value)
            else:
                self._pc.set(value)
            return
        with self._lock:
            self._values[self._key(labels)] = value

    def inc(self, labels: dict[str, str] | None = None, amount: float = 1) -> None:
        amount = float(amount)
        if self._pc is not None:
            if self.labelnames:
                self._pc.labels(**_filter_labels(self, labels)).inc(amount)
            else:
                self._pc.inc(amount)
            return
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, labels: dict[str, str] | None = None, amount: float = 1) -> None:
        self.inc(labels, -amount)

    def _samples(self) -> list[tuple[dict[str, str], str, float]]:
        with self._lock:
            return [
                (_label_key_to_dict(key), "", float(value))
                for key, value in self._values.items()
            ]


class Histogram(Metric):
    """Prometheus-style histogram with default buckets in seconds."""

    _type = "histogram"

    def __init__(
        self,
        name: str,
        doc: str,
        labelnames: tuple[str, ...] = (),
        buckets: tuple[float, ...] = (
            0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
        ),
    ) -> None:
        super().__init__(name, doc, labelnames)
        self.buckets = buckets
        if _PROMETHEUS_AVAILABLE:
            self._pc = _PCHistogram(
                name, doc, labelnames, buckets=buckets, registry=REGISTRY._pc_registry
            )
        else:
            self._pc = None

    def observe(self, labels: dict[str, str] | None = None, value: float = 0) -> None:
        value = float(value)
        if self._pc is not None:
            if self.labelnames:
                self._pc.labels(**_filter_labels(self, labels)).observe(value)
            else:
                self._pc.observe(value)
            return
        key = self._key(labels)
        with self._lock:
            self._values.setdefault(key, []).append(value)

    def _samples(self) -> list[tuple[dict[str, str], str, float]]:
        samples: list[tuple[dict[str, str], str, float]] = []
        with self._lock:
            for key, values in self._values.items():
                base_labels = _label_key_to_dict(key)
                counts = {b: 0 for b in self.buckets}
                for v in values:
                    for b in self.buckets:
                        if v <= b:
                            counts[b] += 1
                for b in self.buckets:
                    samples.append((base_labels | {"le": str(b)}, "_bucket", float(counts[b])))
                total = len(values)
                total_sum = sum(values)
                samples.append((base_labels | {"le": "+Inf"}, "_bucket", float(total)))
                samples.append((base_labels, "_count", float(total)))
                samples.append((base_labels, "_sum", float(total_sum)))
        return samples


# Core HTTP request metrics recorded by MetricMiddleware.
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total", "Total HTTP requests", ("method", "status")
)
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds", "HTTP request duration", ("method", "status")
)
HTTP_REQUESTS_IN_FLIGHT = Gauge(
    "http_requests_in_flight", "HTTP requests in flight", ("method",)
)

# Application-specific metrics.
KAFKA_PRODUCED_TOTAL = Counter(
    "kafka_produced_total", "Total Kafka messages produced", ("topic", "transport")
)
KAFKA_CONSUMED_TOTAL = Counter(
    "kafka_consumed_total", "Total Kafka messages consumed", ("topic", "transport")
)
CROSS_AGENT_CALLS_TOTAL = Counter(
    "cross_agent_calls_total", "Total cross-agent calls", ("client", "status")
)


class MetricMiddleware(BaseHTTPMiddleware):
    """Record request count, duration, and in-flight requests for Prometheus."""

    async def dispatch(self, request, call_next):
        method = request.method
        HTTP_REQUESTS_IN_FLIGHT.inc({"method": method})
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            duration = time.perf_counter() - t0
            status = getattr(exc, "status_code", 500) or 500
            labels = {"method": method, "status": str(status)}
            HTTP_REQUESTS_IN_FLIGHT.dec({"method": method})
            HTTP_REQUESTS_TOTAL.inc(labels)
            HTTP_REQUEST_DURATION.observe(labels, duration)
            raise
        duration = time.perf_counter() - t0
        labels = {"method": method, "status": str(response.status_code)}
        HTTP_REQUESTS_IN_FLIGHT.dec({"method": method})
        HTTP_REQUESTS_TOTAL.inc(labels)
        HTTP_REQUEST_DURATION.observe(labels, duration)
        return response


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    items = sorted(labels.items())
    return ",".join(f'{k}="{_escape(v)}"' for k, v in items)


def generate_metrics() -> str:
    """Render all registered metrics in Prometheus exposition format."""
    if _PROMETHEUS_AVAILABLE:
        return generate_latest(REGISTRY._pc_registry).decode("utf-8")

    lines: list[str] = []
    for metric in REGISTRY._metrics:
        lines.append(f"# HELP {metric.name} {metric.doc}")
        lines.append(f"# TYPE {metric.name} {metric._type}")
        for labels, suffix, value in metric._samples():
            full_name = metric.name + suffix
            label_str = _render_labels(labels)
            if label_str:
                lines.append(f"{full_name}{{{label_str}}} {value}")
            else:
                lines.append(f"{full_name} {value}")
    if lines:
        lines.append("")
    return "\n".join(lines)
