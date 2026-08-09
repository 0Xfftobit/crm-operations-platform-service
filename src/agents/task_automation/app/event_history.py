"""Historical event store for blast-radius dry-run (last 7 days)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any
from uuid import uuid4

_events: list[dict[str, Any]] = []

_lock = RLock()


def reset_event_history() -> None:
    with _lock:
        _events.clear()


def record_event(*, topic: str, payload: dict[str, Any], source: str = "ingest") -> dict[str, Any]:
    row = {
        "history_id": f"hist_{uuid4().hex[:12]}",
        "topic": topic,
        "payload": payload,
        "source": source,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    with _lock:
        _events.append(row)
    return row


def list_events(
    *,
    topic: str | None = None,
    days: int = 7,
    tenant_id: str | None = None,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    with _lock:
        all_events = list(_events)
    rows = []
    for e in all_events:
        ts = datetime.fromisoformat(e["recorded_at"].replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts < cutoff:
            continue
        if topic and e.get("topic") != topic:
            continue
        if tenant_id and e.get("payload", {}).get("tenant_id") != tenant_id:
            continue
        rows.append(e)
    return rows[-limit:]
