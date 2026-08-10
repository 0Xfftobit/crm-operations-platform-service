"""Owner/LO notifications for high-value contact events (FR-016).

Falls back to an in-process event log when no notification service is configured.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from threading import RLock
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_log: list[dict[str, Any]] = []
_lock = RLock()


def reset() -> None:
    with _lock:
        _log.clear()


def _headers() -> dict[str, str]:
    settings = get_settings()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.notification_api_key:
        headers["X-API-Key"] = settings.notification_api_key
    return headers


def _payload(contact_id: str, owner_id: str, message: str, *, kind: str, enrollment_id: str,
             step_id: str = "", channel: str = "") -> dict[str, Any]:
    return {
        "contact_id": contact_id,
        "owner_id": owner_id,
        "kind": kind,
        "message": message,
        "enrollment_id": enrollment_id,
        "step_id": step_id,
        "channel": channel,
        "notified_at": datetime.now(UTC).isoformat(),
    }


def notify_owner_of_reply(contact_id: str, owner_id: str, *, enrollment_id: str, step_id: str,
                          channel: str, body: str = "") -> dict[str, Any]:
    """Notify the assigned LO that a contact has replied to an outreach step."""
    message = f"Contact {contact_id} replied on {channel} (step {step_id})."
    if body:
        message += f" Preview: {body[:200]}"
    payload = _payload(contact_id, owner_id, message, kind="reply", enrollment_id=enrollment_id,
                       step_id=step_id, channel=channel)

    settings = get_settings()
    if settings.notification_service_url and owner_id:
        try:
            with httpx.Client(timeout=settings.integration_timeout_s) as client:
                resp = client.post(
                    f"{settings.notification_service_url.rstrip('/')}/notify",
                    headers=_headers(),
                    json=payload,
                )
            if resp.is_success:
                data = resp.json()
                data.setdefault("provider", "notification_http")
                return data
            logger.warning("notify_owner_failed: status=%s body=%s", resp.status_code, resp.text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("notify_owner_error: %s", exc)

    with _lock:
        _log.append(payload)
    return {"notified": bool(owner_id), "provider": "notification_log", "payload": payload}


def notification_log() -> list[dict[str, Any]]:
    with _lock:
        return list(_log)
