"""Duplicate task conflict detection."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


def has_recent_duplicate(
    tasks: list[dict[str, Any]],
    *,
    lead_id: str,
    task_type: str,
    window_hours: float,
    now: datetime | None = None,
) -> bool:
    """True if a pending/in-progress task of same type exists within the window."""
    ref = now or datetime.now(UTC)
    cutoff = ref - timedelta(hours=window_hours)
    for task in tasks:
        if task.get("lead_id") != lead_id:
            continue
        if task.get("task_type") != task_type:
            continue
        if task.get("status") in ("completed", "cancelled"):
            continue
        created = datetime.fromisoformat(str(task["created_at"]).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if created >= cutoff:
            return True
    return False
