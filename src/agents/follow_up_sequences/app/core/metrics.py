"""Prometheus-style metrics with an in-memory fallback.

If ``prometheus_client`` is installed the real library is used; otherwise the
same public API is backed by thread-safe in-memory data structures and
``generate_metrics()`` produces Prometheus exposition text manually.
"""
from __future__ import annotations

import time
from threading import RLock
from typing import Any

try:
    from prometheus_client import (  # type: ignore[import-not-found]
        CollectorRegistry,
        generate_latest,
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

    _HAS_PROMETHEUS = True
except Exception:  # pragma: no cover - prometheus optional
    _HAS_PROMETHEUS = False


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


_DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class _InMemoryRegistry:
    def __init__(self) -> None:
        self._metrics: list[Any] = []
        self._lock = RLock()

    def register(self, metric: Any) -> None:
        with self._lock:
            self._metrics.append(metric)

    def generate(self) -> str:
        with self._lock:
            parts: list[str] = []
            for metric in sorted(self._metrics, key=lambda m: m.name):
                parts.extend(metric._format_lines())
            if parts:
                parts.append("")
            return "\n".join(parts)


if _HAS_PROMETHEUS:
    REGISTRY: CollectorRegistry | _InMemoryRegistry = CollectorRegistry()
else:
    REGISTRY = _InMemoryRegistry()


class _Child:
    def __init__(self, family: _MetricFamily, key: tuple[str, ...]) -> None:
        self._family = family
        self._key = key

    def inc(self, amount: float = 1) -> None:
        self._family._inc(self._key, amount)

    def dec(self, amount: float = 1) -> None:
        self._family._inc(self._key, -amount)

    def set(self, value: float) -> None:
        self._family._set(self._key, value)

    def observe(self, value: float) -> None:
        self._family._observe(self._key, value)


class _MetricFamily:
    _type = "untyped"

    def __init__(
        self,
        name: str,
        description: str,
        labelnames: tuple[str, ...] = (),
        registry: Any = None,
    ) -> None:
        self.name = name
        self.description = description
        self.labelnames = labelnames
        self._samples: dict[tuple[str, ...], Any] = {}
        self._children: dict[tuple[str, ...], _Child] = {}
        self._lock = RLock()
        if registry is not None:
            registry.register(self)

    def _label_str(self, key: tuple[str, ...]) -> str:
        if not self.labelnames:
            return ""
        parts = [f'{ln}="{_escape_label(str(v))}"' for ln, v in zip(self.labelnames, key)]
        return ",".join(parts)

    def _child(self, key: tuple[str, ...]) -> _Child:
        with self._lock:
            if key not in self._children:
                self._children[key] = _Child(self, key)
            if key not in self._samples:
                self._samples[key] = self._new_sample()
            return self._children[key]

    def labels(self, **kwargs: Any) -> _Child:
        key = tuple(str(kwargs.get(ln, "")) for ln in self.labelnames)
        return self._child(key)

    def inc(self, amount: float = 1) -> None:
        if self.labelnames:
            raise ValueError(f"Metric {self.name} has labels; use .labels(...).inc()")
        self._child(()).inc(amount)

    def dec(self, amount: float = 1) -> None:
        if self.labelnames:
            raise ValueError(f"Metric {self.name} has labels; use .labels(...).dec()")
        self._child(()).dec(amount)

    def set(self, value: float) -> None:
        if self.labelnames:
            raise ValueError(f"Metric {self.name} has labels; use .labels(...).set()")
        self._child(()).set(value)

    def observe(self, value: float) -> None:
        if self.labelnames:
            raise ValueError(f"Metric {self.name} has labels; use .labels(...).observe()")
        self._child(()).observe(value)

    def _new_sample(self) -> Any:
        raise NotImplementedError

    def _format_lines(self) -> list[str]:
        raise NotImplementedError


class _InMemoryCounter(_MetricFamily):
    _type = "counter"

    def _new_sample(self) -> float:
        return 0.0

    def _inc(self, key: tuple[str, ...], amount: float) -> None:
        with self._lock:
            self._samples[key] = self._samples.get(key, 0.0) + amount

    def _set(self, key: tuple[str, ...], value: float) -> None:  # noqa: ARG002
        raise TypeError("Counter does not support set()")

    def _observe(self, key: tuple[str, ...], value: float) -> None:  # noqa: ARG002
        raise TypeError("Counter does not support observe()")

    def _format_lines(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.description}", f"# TYPE {self.name} counter"]
        with self._lock:
            for key in sorted(self._samples):
                ls = self._label_str(key)
                if ls:
                    lines.append(f"{self.name}{{{ls}}} {self._samples[key]}")
                else:
                    lines.append(f"{self.name} {self._samples[key]}")
        return lines


class _InMemoryGauge(_MetricFamily):
    _type = "gauge"

    def _new_sample(self) -> float:
        return 0.0

    def _inc(self, key: tuple[str, ...], amount: float) -> None:
        with self._lock:
            self._samples[key] = self._samples.get(key, 0.0) + amount

    def _set(self, key: tuple[str, ...], value: float) -> None:
        with self._lock:
            self._samples[key] = value

    def _observe(self, key: tuple[str, ...], value: float) -> None:  # noqa: ARG002
        raise TypeError("Gauge does not support observe()")

    def _format_lines(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.description}", f"# TYPE {self.name} gauge"]
        with self._lock:
            for key in sorted(self._samples):
                ls = self._label_str(key)
                if ls:
                    lines.append(f"{self.name}{{{ls}}} {self._samples[key]}")
                else:
                    lines.append(f"{self.name} {self._samples[key]}")
        return lines


class _InMemoryHistogram(_MetricFamily):
    _type = "histogram"

    def _new_sample(self) -> dict[str, Any]:
        return {
            "_count": 0.0,
            "_sum": 0.0,
            "_buckets": {b: 0.0 for b in _DEFAULT_BUCKETS},
            "_inf": 0.0,
        }

    def _inc(self, key: tuple[str, ...], amount: float) -> None:  # noqa: ARG002
        raise TypeError("Histogram does not support inc()")

    def _set(self, key: tuple[str, ...], value: float) -> None:  # noqa: ARG002
        raise TypeError("Histogram does not support set()")

    def _observe(self, key: tuple[str, ...], value: float) -> None:
        with self._lock:
            sample = self._samples[key]
            sample["_count"] += 1
            sample["_sum"] += value
            for b in _DEFAULT_BUCKETS:
                if value <= b:
                    sample["_buckets"][b] += 1
            sample["_inf"] += 1

    def _format_lines(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.description}", f"# TYPE {self.name} histogram"]
        with self._lock:
            for key in sorted(self._samples):
                ls = self._label_str(key)
                opening = f"{self.name}_bucket{{{ls + (',' if ls else '')}le="
                for b in _DEFAULT_BUCKETS:
                    if b == int(b):
                        bstr = str(int(b))
                    else:
                        bstr = str(b)
                    value = self._samples[key]["_buckets"][b]
                    lines.append(f'{opening}"{bstr}"}} {value}')
                lines.append(f'{opening}"+Inf"}} {self._samples[key]["_inf"]}')
                if ls:
                    lines.append(f"{self.name}_sum{{{ls}}} {self._samples[key]['_sum']}")
                    lines.append(f"{self.name}_count{{{ls}}} {self._samples[key]['_count']}")
                else:
                    lines.append(f"{self.name}_sum {self._samples[key]['_sum']}")
                    lines.append(f"{self.name}_count {self._samples[key]['_count']}")
        return lines


if _HAS_PROMETHEUS:

    def Counter(  # noqa: N802
        name: str,
        description: str,
        labelnames: tuple[str, ...] | list[str] = (),
        registry: Any = REGISTRY,
    ) -> Any:
        return _PromCounter(name, description, labelnames=list(labelnames), registry=registry)

    def Histogram(  # noqa: N802
        name: str,
        description: str,
        labelnames: tuple[str, ...] | list[str] = (),
        registry: Any = REGISTRY,
    ) -> Any:
        return _PromHistogram(name, description, labelnames=list(labelnames), registry=registry)

    def Gauge(  # noqa: N802
        name: str,
        description: str,
        labelnames: tuple[str, ...] | list[str] = (),
        registry: Any = REGISTRY,
    ) -> Any:
        return _PromGauge(name, description, labelnames=list(labelnames), registry=registry)

    def generate_metrics() -> str:
        return generate_latest(REGISTRY).decode("utf-8")

else:

    def Counter(  # noqa: N802
        name: str,
        description: str,
        labelnames: tuple[str, ...] | list[str] = (),
        registry: Any = REGISTRY,
    ) -> _InMemoryCounter:
        ln = tuple(labelnames)
        return _InMemoryCounter(name, description, ln, registry=registry)

    def Histogram(  # noqa: N802
        name: str,
        description: str,
        labelnames: tuple[str, ...] | list[str] = (),
        registry: Any = REGISTRY,
    ) -> _InMemoryHistogram:
        ln = tuple(labelnames)
        return _InMemoryHistogram(name, description, ln, registry=registry)

    def Gauge(  # noqa: N802
        name: str,
        description: str,
        labelnames: tuple[str, ...] | list[str] = (),
        registry: Any = REGISTRY,
    ) -> _InMemoryGauge:
        ln = tuple(labelnames)
        return _InMemoryGauge(name, description, ln, registry=registry)

    def generate_metrics() -> str:
        return REGISTRY.generate()


# Application-level metrics
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests handled by the agent",
    ["method", "status"],
    registry=REGISTRY,
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "status"],
    registry=REGISTRY,
)

HTTP_REQUESTS_IN_FLIGHT = Gauge(
    "http_requests_in_flight",
    "Number of HTTP requests currently being processed",
    registry=REGISTRY,
)

KAFKA_PRODUCED_TOTAL = Counter(
    "kafka_produced_total",
    "Total Kafka messages produced",
    ["topic"],
    registry=REGISTRY,
)

KAFKA_CONSUMED_TOTAL = Counter(
    "kafka_consumed_total",
    "Total Kafka messages consumed",
    ["topic"],
    registry=REGISTRY,
)

CROSS_AGENT_CALLS_TOTAL = Counter(
    "cross_agent_calls_total",
    "Total cross-agent HTTP calls",
    ["client", "status"],
    registry=REGISTRY,
)


class MetricMiddleware:
    """ASGI middleware that records request counts, durations, and in-flight requests."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        HTTP_REQUESTS_IN_FLIGHT.inc()
        start = time.perf_counter()
        status_code = "500"

        async def wrapped_send(message: Any) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = str(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        finally:
            HTTP_REQUESTS_IN_FLIGHT.dec()
            duration = time.perf_counter() - start
            method = scope.get("method", "UNKNOWN")
            HTTP_REQUEST_DURATION.labels(method=method, status=status_code).observe(duration)
            HTTP_REQUESTS_TOTAL.labels(method=method, status=status_code).inc()
