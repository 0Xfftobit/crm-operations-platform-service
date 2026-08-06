"""CRM record mutation stub — stage fields, owner, flags."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def apply_record_updates(
    *,
    lead_id: str,
    updates: dict[str, Any],
    event_payload: dict[str, Any],
) -> dict[str, Any]:
    """Apply CRM hygiene updates via CRM service API (stub)."""
    applied: dict[str, Any] = {"lead_id": lead_id}
    if updates.get("owner_id"):
        applied["owner_id"] = updates["owner_id"]
    elif event_payload.get("agent_id"):
        applied["owner_id"] = event_payload["agent_id"]
    if updates.get("stage_field") and updates.get("stage_value"):
        applied[updates["stage_field"]] = updates["stage_value"]
    flags = list(updates.get("flags") or [])
    if flags:
        applied["flags"] = flags
    return {
        "mutation_id": f"mut_{uuid4().hex[:12]}",
        "lead_id": lead_id,
        "applied": applied,
        "provider": "crm_stub",
        "updated_at": datetime.now(UTC).isoformat(),
    }


def assign_owner(*, lead_id: str, owner_id: str, reason: str | None = None) -> dict[str, Any]:
    return {
        "mutation_id": f"mut_{uuid4().hex[:12]}",
        "lead_id": lead_id,
        "owner_id": owner_id,
        "reason": reason,
        "action": "assign_owner",
        "provider": "crm_stub",
        "updated_at": datetime.now(UTC).isoformat(),
    }


def post_note(*, lead_id: str, body: str, author: str = "system", visibility: str = "internal") -> dict[str, Any]:
    return {
        "note_id": f"note_{uuid4().hex[:12]}",
        "lead_id": lead_id,
        "body": body,
        "author": author,
        "visibility": visibility,
        "provider": "crm_stub",
        "posted_at": datetime.now(UTC).isoformat(),
    }
