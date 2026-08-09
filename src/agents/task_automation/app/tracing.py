"""Distributed tracing — ingest → enrich → rule → action (§10)."""
from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from threading import RLock
from typing import Any

_traces: list[dict[str, Any]] = []
_active_spans: list[str] = []

_lock = RLock()


def reset_traces() -> None:
    with _lock:
        _traces.clear()
        _active_spans.clear()


def get_traces(*, limit: int = 30) -> list[dict[str, Any]]:
    with _lock:
        return list(reversed(_traces[-limit:]))


@contextmanager
def trace_span(name: str, **attrs: Any) -> Iterator[dict[str, Any]]:
    started = time.perf_counter()
    with _lock:
        _active_spans.append(name)
    span: dict[str, Any] = {"span": name, **attrs}
    try:
        yield span
    finally:
        span["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        with _lock:
            _active_spans.pop()
            _traces.append(span)


def trace_pipeline(name: str) -> Callable:
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with trace_span(f"pipeline.{name}"):
                return fn(*args, **kwargs)

        return wrapper

    return decorator


def active_spans() -> list[str]:
    with _lock:
        return list(_active_spans)
