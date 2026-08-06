"""In-memory persistence for rules, CRM tasks, and audit trail."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.business_hours import is_overdue
from app.persistence import redis_store
from app.persistence.db import fetch_all_rules, fetch_rules_for_event, persist_rule
from app.schemas.enums import PRIORITY_RANK, TaskStatus

_rules: dict[str, dict[str, Any]] = {}
_tasks: dict[str, dict[str, Any]] = {}
_audit: list[dict[str, Any]] = []
_rules_hydrated = False


def hydrate_rules_from_db() -> int:
    """Load rule registry from Postgres/SQLite into memory cache."""
    global _rules_hydrated
    from app.core.config import get_settings

    if get_settings().storage_backend not in ("sqlite", "postgres"):
        _rules_hydrated = True
        return len(_rules)
    loaded = fetch_all_rules()
    for row in loaded:
        _rules[row["rule_id"]] = row
    _rules_hydrated = True
    return len(loaded)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _task_title(task_type: str, lead_id: str) -> str:
    labels = {
        "call": "Call lead",
        "whatsapp": "WhatsApp follow-up",
        "send_email": "Send email",
        "send_document": "Send document",
        "schedule_meeting": "Schedule meeting",
    }
    return f"{labels.get(task_type, 'Task')} — {lead_id}"


def _enrich_task(row: dict[str, Any]) -> dict[str, Any]:
    overdue = row.get("status") not in ("completed", "cancelled") and is_overdue(row["due_at"])
    out = {**row, "overdue": overdue}
    if overdue:
        out["status"] = TaskStatus.OVERDUE
        out["alert_level"] = "red"
    else:
        out["alert_level"] = None
    return out


def record_audit(
    *,
    lead_id: str,
    action: str,
    detail: dict[str, Any] | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    row = {
        "audit_id": f"aud_{uuid4().hex[:12]}",
        "lead_id": lead_id,
        "action": action,
        "detail": detail or {},
        "actor": actor,
        "recorded_at": _now(),
    }
    _audit.append(row)
    return row


# ── Rules ─────────────────────────────────────────────────────────────────────

def create_rule(data: dict[str, Any]) -> dict[str, Any]:
    rule_id = f"rule_{uuid4().hex[:12]}"
    row = {
        "rule_id": rule_id,
        "created_at": _now(),
        "updated_at": _now(),
        **data,
    }
    _rules[rule_id] = row
    persist_rule(row)
    version = str(row.get("version") or "1")
    redis_store.cache_rule_version(rule_id, version, row)
    redis_store.cache_rule(rule_id, row)
    return row


def list_rules(*, active_only: bool = False) -> list[dict[str, Any]]:
    rows = list(_rules.values())
    if active_only:
        rows = [r for r in rows if r.get("active", True)]
    return sorted(rows, key=lambda r: r["created_at"])


def get_rule(rule_id: str) -> dict[str, Any] | None:
    if not _rules_hydrated:
        hydrate_rules_from_db()
    row = _rules.get(rule_id)
    if row:
        return row
    cached = redis_store.get_cached_rule(rule_id)
    if cached:
        _rules[rule_id] = cached
        return cached
    return None


def get_rule_cached(rule_id: str, version: str | None = None) -> dict[str, Any] | None:
    row = _rules.get(rule_id)
    if not row:
        return None
    ver = version or str(row.get("version") or "1")
    cached = redis_store.get_cached_rule_version(rule_id, ver)
    return cached or row


def update_rule(rule_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    if not _rules_hydrated:
        hydrate_rules_from_db()
    row = _rules.get(rule_id)
    if not row:
        return None
    row.update({k: v for k, v in patch.items() if v is not None})
    row["updated_at"] = _now()
    version = str(row.get("version") or "1")
    persist_rule(row)
    redis_store.cache_rule_version(rule_id, version, row)
    redis_store.invalidate_rule_cache(rule_id)
    redis_store.cache_rule(rule_id, row)
    return row


def delete_rule(rule_id: str) -> bool:
    return _rules.pop(rule_id, None) is not None


def rules_for_event(
    trigger_event: str,
    *,
    include_draft: bool = False,
    tenant_id: str | None = None,
    feature_flags: set[str] | None = None,
) -> list[dict[str, Any]]:
    from app.core.config import get_settings

    if get_settings().storage_backend in ("sqlite", "postgres"):
        rows = fetch_rules_for_event(
            trigger_event, include_draft=include_draft, tenant_id=tenant_id,
        )
        for row in rows:
            _rules[row["rule_id"]] = row
    else:
        if not _rules_hydrated:
            hydrate_rules_from_db()

        def _matches(r: dict[str, Any]) -> bool:
            event_types = r.get("event_types") or []
            if event_types:
                return trigger_event in event_types
            return r.get("trigger_event") == trigger_event

        rows = [r for r in _rules.values() if _matches(r) and r.get("active", True)]
        if not include_draft:
            rows = [r for r in rows if r.get("rollout_status", "active") in ("active", "canary")]
        if tenant_id:
            rows = [r for r in rows if not r.get("tenant_id") or r.get("tenant_id") == tenant_id]

    if feature_flags is not None:
        rows = [
            r for r in rows
            if not r.get("feature_flag") or r.get("feature_flag") in feature_flags
        ]
    return rows


def record_dry_run(rule_id: str, *, passed: bool) -> dict[str, Any] | None:
    row = _rules.get(rule_id)
    if not row:
        return None
    row["dry_run_passed"] = passed
    row["dry_run_at"] = _now()
    return row


# ── CRM Tasks ─────────────────────────────────────────────────────────────────

def create_crm_task(
    *,
    lead_id: str,
    task_type: str,
    assignee_id: str,
    due_at: str,
    priority: str = "normal",
    rule_id: str | None = None,
    title: str | None = None,
    payload: dict[str, Any] | None = None,
    calendar_event_id: str | None = None,
    recurring: bool = False,
    recurrence_interval_hours: float | None = None,
) -> dict[str, Any]:
    task_id = f"crm_{uuid4().hex[:12]}"
    row = {
        "task_id": task_id,
        "lead_id": lead_id,
        "task_type": task_type,
        "title": title or _task_title(task_type, lead_id),
        "assignee_id": assignee_id,
        "priority": priority,
        "status": TaskStatus.PENDING,
        "due_at": due_at,
        "rule_id": rule_id,
        "payload": payload or {},
        "calendar_event_id": calendar_event_id,
        "recurring": recurring,
        "recurrence_interval_hours": recurrence_interval_hours,
        "created_at": _now(),
        "updated_at": _now(),
        "completed_at": None,
        "outcome_notes": None,
        "outcome": None,
    }
    _tasks[task_id] = row
    record_audit(
        lead_id=lead_id,
        action="task.created",
        detail={"task_id": task_id, "task_type": task_type, "rule_id": rule_id},
        actor=assignee_id,
    )
    return _enrich_task(row)


def get_task(task_id: str) -> dict[str, Any] | None:
    row = _tasks.get(task_id)
    return _enrich_task(row) if row else None


def list_tasks(
    *,
    assignee_id: str | None = None,
    lead_id: str | None = None,
    status: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    rows = [_enrich_task(t) for t in _tasks.values()]
    if assignee_id:
        rows = [r for r in rows if r.get("assignee_id") == assignee_id]
    if lead_id:
        rows = [r for r in rows if r.get("lead_id") == lead_id]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    rows.sort(key=lambda r: (PRIORITY_RANK.get(r.get("priority", "normal"), 2), r.get("due_at", "")))
    return rows[:limit]


def complete_task(
    task_id: str,
    *,
    outcome_notes: str,
    outcome: str | None = None,
    actor: str | None = None,
) -> dict[str, Any] | None:
    row = _tasks.get(task_id)
    if not row:
        return None
    row["status"] = TaskStatus.COMPLETED
    row["completed_at"] = _now()
    row["updated_at"] = row["completed_at"]
    row["outcome_notes"] = outcome_notes
    row["outcome"] = outcome
    record_audit(
        lead_id=row["lead_id"],
        action="task.completed",
        detail={"task_id": task_id, "outcome": outcome, "notes": outcome_notes},
        actor=actor or row.get("assignee_id"),
    )
    return _enrich_task(row)


def all_tasks_raw() -> list[dict[str, Any]]:
    return list(_tasks.values())


def audit_for_lead(lead_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = [a for a in _audit if a.get("lead_id") == lead_id]
    return sorted(rows, key=lambda r: r["recorded_at"], reverse=True)[:limit]


def all_audit(*, limit: int = 200) -> list[dict[str, Any]]:
    return sorted(_audit, key=lambda r: r["recorded_at"], reverse=True)[:limit]


def reset_store() -> None:
    global _rules_hydrated
    _rules.clear()
    _tasks.clear()
    _audit.clear()
    _rules_hydrated = False
