"""Calendar integration for meeting-type tasks.

Falls back to a synthetic event id when CALENDAR_SERVICE_URL is not configured.
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
    if settings.calendar_api_key:
        headers["X-API-Key"] = settings.calendar_api_key
    return headers


def create_calendar_event(
    *,
    lead_id: str,
    assignee_id: str,
    due_at: str,
    title: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a calendar event via the configured calendar service, else stub."""
    settings = get_settings()
    body = {
        "lead_id": lead_id,
        "assignee_id": assignee_id,
        "scheduled_at": due_at,
        "title": title,
        "payload": payload or {},
    }

    if settings.calendar_service_url:
        try:
            with httpx.Client(timeout=settings.integration_timeout_s) as client:
                resp = client.post(
                    f"{settings.calendar_service_url}/events",
                    headers=_headers(),
                    json=body,
                )
            if resp.is_success:
                data = resp.json()
                data.setdefault("provider", "calendar_http")
                data.setdefault("created_at", datetime.now(UTC).isoformat())
                return data
            logger.warning("calendar_create_failed", status=resp.status_code, body=resp.text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("calendar_create_error", error=str(exc))

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
