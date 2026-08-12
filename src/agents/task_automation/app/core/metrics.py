"""Prometheus-style metrics with an in-memory registry.

If ``prometheus_client`` is installed it is used only for exposition formatting;
all metric values are kept in memory so the test suite can reset state quickly
and no live infrastructure is required.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

PROMETHEUS_AVAILABLE = False

try:
    from prometheus_client import CollectorRegistry

    PROMETHEUS_AVAILABLE = True
except Exception:  # pragma: no cover
    pass

_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
_METRICS: list[Metric] = []
_METRICS_LOCK = threading.Lock()

if PROMETHEUS_AVAILABLE:
    REGISTRY = CollectorRegistry()
else:
    REGISTRY = None


class Metric:
    """Base metric wrapper."""

    def __init__(
        self,
        name: str,
        description: str,
        metric_type: str,
        labels: list[str] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.metric_type = metric_type
        self.labels = labels or []
        self._lock = threading.Lock()
        with _METRICS_LOCK:
            _METRICS.append(self)

    def reset(self) -> None:
        raise NotImplementedError

    def _label_key(self, **kwargs: Any) -> tuple[str, ...]:
        return tuple(str(kwargs.get(label, "")) for label in self.labels)

    def _format_labels(self, key: tuple[str, ...]) -> str:
        return ",".join(f'{label}="{value}"' for label, value in zip(self.labels, key))

    def _exposition(self) -> list[str]:
        raise NotImplementedError


class Counter(Metric):
    """Counter that only increases."""

    def __init__(self, name: str, description: str, labels: list[str] | None = None) -> None:
        super().__init__(name, description, "counter", labels)
        self._values: dict[tuple[str, ...], int] = {}

    def inc(self, **kwargs: Any) -> None:
        key = self._label_key(**kwargs)
        with self._lock:
            self._values[key] = self._values.get(key, 0) + 1

    def reset(self) -> None:
        with self._lock:
            self._values = {}

    def _exposition(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.description}", f"# TYPE {self.name} counter"]
        for key, value in self._values.items():
            labels = self._format_labels(key)
            if labels:
                lines.append(f"{self.name}{{{labels}}} {value}")
            else:
                lines.append(f"{self.name} {value}")
        return lines


class Gauge(Metric):
    """Gauge that can go up and down."""

    def __init__(self, name: str, description: str, labels: list[str] | None = None) -> None:
        super().__init__(name, description, "gauge", labels)
        self._values: dict[tuple[str, ...], float] = {}

    def set(self, value: float, **kwargs: Any) -> None:
        key = self._label_key(**kwargs)
        with self._lock:
            self._values[key] = float(value)

    def inc(self, **kwargs: Any) -> None:
        key = self._label_key(**kwargs)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + 1.0

    def dec(self, **kwargs: Any) -> None:
        key = self._label_key(**kwargs)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) - 1.0

    def reset(self) -> None:
        with self._lock:
            self._values = {}

    def _exposition(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.description}", f"# TYPE {self.name} gauge"]
        for key, value in self._values.items():
            labels = self._format_labels(key)
            if labels:
                lines.append(f"{self.name}{{{labels}}} {value}")
            else:
                lines.append(f"{self.name} {value}")
        return lines


class Histogram(Metric):
    """Histogram that observes numeric values."""

    def __init__(self, name: str, description: str, labels: list[str] | None = None) -> None:
        super().__init__(name, description, "histogram", labels)
        self._values: dict[tuple[str, ...], list[float]] = {}

    def observe(self, value: float, **kwargs: Any) -> None:
        key = self._label_key(**kwargs)
        with self._lock:
            self._values.setdefault(key, []).append(float(value))

    def reset(self) -> None:
        with self._lock:
            self._values = {}

    def _exposition(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.description}", f"# TYPE {self.name} histogram"]
        for key, values in self._values.items():
            labels = self._format_labels(key)
            total = len(values)
            total_sum = sum(values)
            for bucket in _BUCKETS:
                count = sum(1 for v in values if v <= bucket)
                if labels:
                    lines.append(f'{self.name}_bucket{{{labels},le="{bucket}"}} {count}')
                else:
                    lines.append(f'{self.name}_bucket{{le="{bucket}"}} {count}')
            if labels:
                lines.append(f'{self.name}_bucket{{{labels},le="+Inf"}} {total}')
                lines.append(f"{self.name}_sum{{{labels}}} {total_sum}")
                lines.append(f"{self.name}_count{{{labels}}} {total}")
            else:
                lines.append(f'{self.name}_bucket{{le="+Inf"}} {total}')
                lines.append(f"{self.name}_sum {total_sum}")
                lines.append(f"{self.name}_count {total}")
        return lines


HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "status"],
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "status"],
)

HTTP_REQUESTS_IN_FLIGHT = Gauge(
    "http_requests_in_flight",
    "HTTP requests currently being processed",
)

KAFKA_PRODUCED_TOTAL = Counter(
    "kafka_produced_total",
    "Total Kafka messages produced",
    ["topic", "transport"],
)

KAFKA_CONSUMED_TOTAL = Counter(
    "kafka_consumed_total",
    "Total Kafka messages consumed",
    ["topic"],
)

CROSS_AGENT_CALLS_TOTAL = Counter(
    "cross_agent_calls_total",
    "Cross-agent HTTP client calls",
    ["client", "status"],
)


def inc_kafka_produced(*, topic: str, transport: str) -> None:
    KAFKA_PRODUCED_TOTAL.inc(topic=topic, transport=transport)


def inc_kafka_consumed(*, topic: str) -> None:
    KAFKA_CONSUMED_TOTAL.inc(topic=topic)


def inc_cross_agent_calls(*, client: str, status: str) -> None:
    CROSS_AGENT_CALLS_TOTAL.inc(client=client, status=status)


def reset_metrics() -> None:
    """Clear all metric values (test helper)."""
    with _METRICS_LOCK:
        for metric in _METRICS:
            metric.reset()


def generate_metrics() -> str:
    """Return Prometheus exposition text."""
    lines: list[str] = []
    with _METRICS_LOCK:
        for metric in _METRICS:
            lines.extend(metric._exposition())
    if not lines:
        return "\n"
    return "\n".join(lines) + "\n"


class MetricMiddleware(BaseHTTPMiddleware):
    """Record per-request HTTP metrics."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        method = request.method
        HTTP_REQUESTS_IN_FLIGHT.inc()
        status = "500"
        start = time.perf_counter()
        try:
            response = await call_next(request)
            status = str(response.status_code)
        except Exception:
            status = "500"
            raise
        finally:
            duration = time.perf_counter() - start
            HTTP_REQUEST_DURATION.observe(duration, method=method, status=status)
            HTTP_REQUESTS_TOTAL.inc(method=method, status=status)
            HTTP_REQUESTS_IN_FLIGHT.dec()
        return response  # type: ignore[return-value]
