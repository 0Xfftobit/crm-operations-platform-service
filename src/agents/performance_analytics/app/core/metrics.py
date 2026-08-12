"""Prometheus metrics with an in-memory fallback for broker-free runs.

If ``prometheus_client`` is installed, metrics are backed by a real
``CollectorRegistry`` and exposed via ``generate_latest``. Otherwise the module
falls back to thread-safe in-memory data structures and generates Prometheus
exposition text manually.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

# ── Prometheus client (optional) ─────────────────────────────────────────────

_PC = None
try:
    import prometheus_client as _PC

    # Use a dedicated registry so the endpoint only returns metrics we own.
    REGISTRY: Any | None = _PC.CollectorRegistry()
    _PROMETHEUS_AVAILABLE = True
except Exception:  # noqa: BLE001
    REGISTRY = None
    _PROMETHEUS_AVAILABLE = False


# ── Helpers ──────────────────────────────────────────────────────────────────


def _format_label_pairs(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    pairs = []
    for key in sorted(labels):
        value = labels[key]
        if not isinstance(value, str):
            value = str(value)
        value = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        pairs.append(f'{key}="{value}"')
    return "{" + ",".join(pairs) + "}"


_DEFAULT_BUCKETS = [
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
]


class _Metric:
    """Base metric wrapper."""

    def __init__(self, name: str, documentation: str, labelnames: list[str]) -> None:
        self.name = name
        self.documentation = documentation
        self.labelnames = labelnames
        self._lock = threading.Lock()

    def _ordered_labels(self, labels: dict[str, str]) -> dict[str, str]:
        return {name: str(labels.get(name, "")) for name in self.labelnames}


class Counter(_Metric):
    """Counter that wraps prometheus_client or a fallback map."""

    def __init__(self, name: str, documentation: str, labelnames: list[str]) -> None:
        super().__init__(name, documentation, labelnames)
        if _PROMETHEUS_AVAILABLE:
            self._metric = _PC.Counter(name, documentation, labelnames, registry=REGISTRY)
        else:
            self._values: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)

    def inc(self, labels: dict[str, str], amount: float = 1) -> None:
        labels = self._ordered_labels(labels)
        if _PROMETHEUS_AVAILABLE:
            kwargs = {k: labels[k] for k in self.labelnames}
            self._metric.labels(**kwargs).inc(amount)
        else:
            key = tuple(sorted(labels.items()))
            with self._lock:
                self._values[key] += amount

    def _collect_lines(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.documentation}", f"# TYPE {self.name} counter"]
        with self._lock:
            for key in sorted(self._values):
                label_dict = dict(key)
                value = self._values[key]
                lines.append(f"{self.name}{_format_label_pairs(label_dict)} {value}")
        return lines


class Histogram(_Metric):
    """Histogram that wraps prometheus_client or a fallback value store."""

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: list[str],
        buckets: list[float] | None = None,
    ) -> None:
        super().__init__(name, documentation, labelnames)
        self.buckets = buckets or _DEFAULT_BUCKETS
        if _PROMETHEUS_AVAILABLE:
            self._metric = _PC.Histogram(
                name,
                documentation,
                labelnames,
                buckets=self.buckets,
                registry=REGISTRY,
            )
        else:
            self._observations: dict[tuple[tuple[str, str], ...], list[float]] = defaultdict(list)

    def observe(self, labels: dict[str, str], value: float) -> None:
        labels = self._ordered_labels(labels)
        if _PROMETHEUS_AVAILABLE:
            kwargs = {k: labels[k] for k in self.labelnames}
            self._metric.labels(**kwargs).observe(value)
        else:
            key = tuple(sorted(labels.items()))
            with self._lock:
                self._observations[key].append(value)

    def _collect_lines(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.documentation}", f"# TYPE {self.name} histogram"]
        with self._lock:
            for key in sorted(self._observations):
                values = self._observations[key]
                label_dict = dict(key)
                for bucket in self.buckets:
                    count = sum(1 for v in values if v <= bucket)
                    bucket_labels = dict(label_dict)
                    bucket_labels["le"] = str(bucket)
                    lines.append(
                        f"{self.name}_bucket{_format_label_pairs(bucket_labels)} {count}"
                    )
                inf_labels = dict(label_dict)
                inf_labels["le"] = "+Inf"
                lines.append(f"{self.name}_bucket{_format_label_pairs(inf_labels)} {len(values)}")
                total = sum(values)
                lines.append(f"{self.name}_sum{_format_label_pairs(label_dict)} {total}")
                lines.append(f"{self.name}_count{_format_label_pairs(label_dict)} {len(values)}")
        return lines


class Gauge(_Metric):
    """Gauge that wraps prometheus_client or a fallback map."""

    def __init__(self, name: str, documentation: str, labelnames: list[str]) -> None:
        super().__init__(name, documentation, labelnames)
        if _PROMETHEUS_AVAILABLE:
            self._metric = _PC.Gauge(name, documentation, labelnames, registry=REGISTRY)
        else:
            self._values: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)

    def inc(self, labels: dict[str, str], amount: float = 1) -> None:
        labels = self._ordered_labels(labels)
        if _PROMETHEUS_AVAILABLE:
            kwargs = {k: labels[k] for k in self.labelnames}
            self._metric.labels(**kwargs).inc(amount)
        else:
            key = tuple(sorted(labels.items()))
            with self._lock:
                self._values[key] += amount

    def dec(self, labels: dict[str, str], amount: float = 1) -> None:
        labels = self._ordered_labels(labels)
        if _PROMETHEUS_AVAILABLE:
            kwargs = {k: labels[k] for k in self.labelnames}
            self._metric.labels(**kwargs).dec(amount)
        else:
            key = tuple(sorted(labels.items()))
            with self._lock:
                self._values[key] -= amount

    def set(self, labels: dict[str, str], value: float) -> None:
        labels = self._ordered_labels(labels)
        if _PROMETHEUS_AVAILABLE:
            kwargs = {k: labels[k] for k in self.labelnames}
            self._metric.labels(**kwargs).set(value)
        else:
            key = tuple(sorted(labels.items()))
            with self._lock:
                self._values[key] = value

    def _collect_lines(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.documentation}", f"# TYPE {self.name} gauge"]
        with self._lock:
            for key in sorted(self._values):
                label_dict = dict(key)
                value = self._values[key]
                lines.append(f"{self.name}{_format_label_pairs(label_dict)} {value}")
        return lines


# ── Application metrics ──────────────────────────────────────────────────────

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "status"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "status"],
)

http_requests_in_flight = Gauge(
    "http_requests_in_flight",
    "Current in-flight HTTP requests",
    ["method"],
)

kafka_produced_total = Counter(
    "kafka_produced_total",
    "Total Kafka messages produced",
    ["topic"],
)

kafka_consumed_total = Counter(
    "kafka_consumed_total",
    "Total Kafka messages consumed",
    ["topic"],
)

cross_agent_calls_total = Counter(
    "cross_agent_calls_total",
    "Total cross-agent HTTP calls",
    ["client"],
)

_METRICS: list[_Metric] = [
    http_requests_total,
    http_request_duration_seconds,
    http_requests_in_flight,
    kafka_produced_total,
    kafka_consumed_total,
    cross_agent_calls_total,
]


def generate_metrics() -> str:
    """Return Prometheus exposition text for all registered metrics."""
    if _PROMETHEUS_AVAILABLE and _PC is not None:
        return _PC.generate_latest(REGISTRY).decode("utf-8")

    lines: list[str] = []
    for metric in _METRICS:
        lines.extend(metric._collect_lines())
    return "\n".join(lines) + "\n"


class MetricMiddleware:
    """ASGI middleware that records per-request Prometheus metrics."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        method = request.method
        http_requests_in_flight.inc({"method": method})

        start = time.perf_counter()
        status = "500"

        async def wrapped_send(message: dict[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = str(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        finally:
            duration = time.perf_counter() - start
            labels = {"method": method, "status": status}
            http_requests_total.inc(labels)
            http_request_duration_seconds.observe(labels, duration)
            http_requests_in_flight.dec({"method": method})
