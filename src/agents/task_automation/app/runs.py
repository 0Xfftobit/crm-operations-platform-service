"""Automation run tracking — status, actions, ledger linkage."""
from __future__ import annotations

from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

_runs: dict[str, dict[str, Any]] = {}

_lock = RLock()


def reset_runs() -> None:
    with _lock:
        _runs.clear()


def create_run(
    *,
    trigger_id: str,
    topic: str,
    lead_id: str,
    source: str = "api",
    dry_run: bool = False,
) -> dict[str, Any]:
    run_id = f"run_{uuid4().hex[:14]}"
    row = {
        "run_id": run_id,
        "trigger_id": trigger_id,
        "topic": topic,
        "lead_id": lead_id,
        "source": source,
        "dry_run": dry_run,
        "status": "running",
        "actions": [],
        "ledger_ids": [],
        "conflicts": [],
        "started_at": datetime.now(UTC).isoformat(),
        "completed_at": None,
        "latency_ms": None,
    }
    with _lock:
        _runs[run_id] = row
    return row


def append_run_action(run_id: str, action: dict[str, Any]) -> None:
    with _lock:
        row = _runs.get(run_id)
        if row:
            row.setdefault("actions", []).append(action)


def record_run_actions(run_id: str, actions: list[dict[str, Any]]) -> None:
    with _lock:
        row = _runs.get(run_id)
        if row:
            row["actions"] = actions


def get_run(run_id: str) -> dict[str, Any] | None:
    with _lock:
        return _runs.get(run_id)


def get_run_by_trigger(trigger_id: str) -> dict[str, Any] | None:
    with _lock:
        return next((r for r in _runs.values() if r.get("trigger_id") == trigger_id), None)


def update_run(run_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    with _lock:
        row = _runs.get(run_id)
        if not row:
            return None
        row.update(patch)
        return row


def complete_run(
    run_id: str,
    *,
    status: str,
    actions: list[dict[str, Any]] | None = None,
    ledger_ids: list[str] | None = None,
    conflicts: list[dict[str, Any]] | None = None,
    latency_ms: float | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    with _lock:
        row = _runs.get(run_id)
        if not row:
            return None
        row["status"] = status
        row["completed_at"] = datetime.now(UTC).isoformat()
        if actions is not None:
            row["actions"] = actions
        if ledger_ids is not None:
            row["ledger_ids"] = ledger_ids
        if conflicts is not None:
            row["conflicts"] = conflicts
        if latency_ms is not None:
            row["latency_ms"] = latency_ms
        if error:
            row["error"] = error
        return row


def list_runs(*, lead_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        rows = list(_runs.values())
    if lead_id:
        rows = [r for r in rows if r.get("lead_id") == lead_id]
    return sorted(rows, key=lambda r: r["started_at"], reverse=True)[:limit]
