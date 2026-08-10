"""CRM record mutation client — stage fields, owner, flags.

Falls back to an in-process synthetic record when CRM_SERVICE_URL is not configured.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _headers() -> dict[str, str]:
    settings = get_settings()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.crm_api_key:
        headers["X-API-Key"] = settings.crm_api_key
    return headers


def apply_record_updates(
    *,
    lead_id: str,
    updates: dict[str, Any],
    event_payload: dict[str, Any],
) -> dict[str, Any]:
    """Apply CRM hygiene updates via CRM service API when configured, else stub."""
    settings = get_settings()
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

    if settings.crm_service_url:
        try:
            with httpx.Client(timeout=settings.integration_timeout_s) as client:
                resp = client.patch(
                    f"{settings.crm_service_url}/leads/{lead_id}",
                    headers=_headers(),
                    json=applied,
                )
            if resp.is_success:
                data = resp.json()
                data.setdefault("provider", "crm_http")
                data.setdefault("updated_at", datetime.now(UTC).isoformat())
                return data
            logger.warning("crm_update_failed", extra={"status": resp.status_code, "body": resp.text})
        except Exception as exc:  # noqa: BLE001
            logger.warning("crm_update_error", extra={"error": str(exc)})

    return {
        "mutation_id": f"mut_{uuid4().hex[:12]}",
        "lead_id": lead_id,
        "applied": applied,
        "provider": "crm_stub",
        "updated_at": datetime.now(UTC).isoformat(),
    }


def assign_owner(*, lead_id: str, owner_id: str, reason: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    if settings.crm_service_url:
        try:
            with httpx.Client(timeout=settings.integration_timeout_s) as client:
                resp = client.post(
                    f"{settings.crm_service_url}/leads/{lead_id}/assign",
                    headers=_headers(),
                    json={"owner_id": owner_id, "reason": reason},
                )
            if resp.is_success:
                data = resp.json()
                data.setdefault("provider", "crm_http")
                data.setdefault("updated_at", datetime.now(UTC).isoformat())
                return data
            logger.warning("crm_assign_failed", extra={"status": resp.status_code, "body": resp.text})
        except Exception as exc:  # noqa: BLE001
            logger.warning("crm_assign_error", extra={"error": str(exc)})

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
    settings = get_settings()
    if settings.crm_service_url:
        try:
            with httpx.Client(timeout=settings.integration_timeout_s) as client:
                resp = client.post(
                    f"{settings.crm_service_url}/leads/{lead_id}/notes",
                    headers=_headers(),
                    json={"body": body, "author": author, "visibility": visibility},
                )
            if resp.is_success:
                data = resp.json()
                data.setdefault("provider", "crm_http")
                data.setdefault("posted_at", datetime.now(UTC).isoformat())
                return data
            logger.warning("crm_note_failed", extra={"status": resp.status_code, "body": resp.text})
        except Exception as exc:  # noqa: BLE001
            logger.warning("crm_note_error", extra={"error": str(exc)})

    return {
        "note_id": f"note_{uuid4().hex[:12]}",
        "lead_id": lead_id,
        "body": body,
        "author": author,
        "visibility": visibility,
        "provider": "crm_stub",
        "posted_at": datetime.now(UTC).isoformat(),
    }
