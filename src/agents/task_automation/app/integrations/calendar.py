"""Calendar integration stub for meeting-type tasks."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def create_calendar_event(
    *,
    lead_id: str,
    assignee_id: str,
    due_at: str,
    title: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stub calendar API — returns synthetic event id for schedule_meeting tasks."""
    return {
        "calendar_event_id": f"cal_{uuid4().hex[:12]}",
        "provider": "stub",
        "lead_id": lead_id,
        "assignee_id": assignee_id,
        "scheduled_at": due_at,
        "title": title,
        "created_at": datetime.now(UTC).isoformat(),
        "payload": payload or {},
    }
