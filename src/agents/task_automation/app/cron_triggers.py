"""Schedule-driven (cron) trigger management."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.service import process_event

_schedules: dict[str, dict[str, Any]] = {}
_fired: list[dict[str, Any]] = []


def reset_cron() -> None:
    _schedules.clear()
    _fired.clear()


def register_schedule(
    *,
    name: str,
    cron: str,
    trigger_event: str,
    payload_template: dict[str, Any] | None = None,
    active: bool = True,
) -> dict[str, Any]:
    schedule_id = f"sched_{uuid4().hex[:10]}"
    row = {
        "schedule_id": schedule_id,
        "name": name,
        "cron": cron,
        "trigger_event": trigger_event,
        "payload_template": payload_template or {},
        "active": active,
        "created_at": datetime.now(UTC).isoformat(),
    }
    _schedules[schedule_id] = row
    return row


def list_schedules() -> list[dict[str, Any]]:
    return list(_schedules.values())


def fire_schedule(schedule_id: str, *, payload_override: dict[str, Any] | None = None) -> dict[str, Any]:
    sched = _schedules.get(schedule_id)
    if not sched:
        return {"error": "schedule_not_found"}
    if not sched.get("active", True):
        return {"error": "schedule_inactive"}
    trigger_id = f"sched_{schedule_id}_{uuid4().hex[:8]}"
    payload = {**sched.get("payload_template", {}), **(payload_override or {}), "trigger_id": trigger_id}
    result = process_event(
        topic=sched["trigger_event"],
        payload=payload,
        trigger_id=trigger_id,
        source="cron",
    )
    fired = {
        "schedule_id": schedule_id,
        "trigger_id": trigger_id,
        "fired_at": datetime.now(UTC).isoformat(),
        "result_summary": {"latency_ms": result.get("latency_ms"), "actions": len(result.get("created") or [])},
    }
    _fired.append(fired)
    return {"schedule": sched, "execution": result, "fired": fired}


def list_fired(*, limit: int = 50) -> list[dict[str, Any]]:
    return list(reversed(_fired[-limit:]))
