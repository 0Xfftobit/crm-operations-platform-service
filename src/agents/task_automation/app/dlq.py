"""Dead-letter queue for failed automation actions with replay."""
from __future__ import annotations

from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

_dlq: list[dict[str, Any]] = []

_lock = RLock()


def enqueue(
    *,
    topic: str,
    payload: dict[str, Any],
    error: str,
    rule_id: str | None = None,
    retry_count: int = 0,
    trigger_id: str | None = None,
) -> dict[str, Any]:
    row = {
        "dlq_id": f"dlq_{uuid4().hex[:12]}",
        "topic": topic,
        "payload": payload,
        "error": error,
        "rule_id": rule_id,
        "retry_count": retry_count,
        "trigger_id": trigger_id,
        "status": "pending",
        "enqueued_at": datetime.now(UTC).isoformat(),
    }
    with _lock:
        _dlq.append(row)
    return row


def get_dlq_entry(dlq_id: str) -> dict[str, Any] | None:
    with _lock:
        return next((r for r in _dlq if r["dlq_id"] == dlq_id), None)


def list_dlq(*, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
    with _lock:
        rows = list(_dlq)
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return list(reversed(rows[-limit:]))


def mark_replayed(dlq_id: str, *, result: dict[str, Any]) -> dict[str, Any] | None:
    with _lock:
        row = next((r for r in _dlq if r["dlq_id"] == dlq_id), None)
        if not row:
            return None
        row["status"] = "replayed"
        row["replayed_at"] = datetime.now(UTC).isoformat()
        row["replay_result"] = result
        row["retry_count"] = int(row.get("retry_count", 0)) + 1
        return row


def reset_dlq() -> None:
    with _lock:
        _dlq.clear()
