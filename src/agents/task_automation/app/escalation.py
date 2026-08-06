"""SLA escalation timers — reassign and notify on breach."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

_timers: dict[str, dict[str, Any]] = {}


def reset_escalation() -> None:
    _timers.clear()


def register_sla_timer(
    *,
    task_id: str,
    lead_id: str,
    assignee_id: str,
    sla_hours: float,
    escalation_assignee: str | None = None,
    rule_id: str | None = None,
) -> dict[str, Any]:
    due = datetime.now(UTC) + timedelta(hours=sla_hours)
    row = {
        "timer_id": f"sla_{uuid4().hex[:10]}",
        "task_id": task_id,
        "lead_id": lead_id,
        "assignee_id": assignee_id,
        "escalation_assignee": escalation_assignee or "ops:queue",
        "rule_id": rule_id,
        "sla_hours": sla_hours,
        "due_at": due.isoformat(),
        "status": "active",
        "breached": False,
    }
    _timers[task_id] = row
    return row


def check_escalations(*, now: datetime | None = None) -> list[dict[str, Any]]:
    """Return breached timers and mark them escalated."""
    now = now or datetime.now(UTC)
    breached: list[dict[str, Any]] = []
    for timer in _timers.values():
        if timer.get("status") != "active":
            continue
        due = datetime.fromisoformat(timer["due_at"].replace("Z", "+00:00"))
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        if now >= due:
            timer["status"] = "escalated"
            timer["breached"] = True
            timer["escalated_at"] = now.isoformat()
            timer["reassigned_to"] = timer["escalation_assignee"]
            breached.append(dict(timer))
    return breached


def list_timers(*, active_only: bool = True) -> list[dict[str, Any]]:
    rows = list(_timers.values())
    if active_only:
        rows = [r for r in rows if r.get("status") == "active"]
    return rows
