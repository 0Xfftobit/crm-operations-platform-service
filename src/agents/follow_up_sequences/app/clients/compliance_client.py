"""Synchronous HTTP client to the Compliance Monitor pre-send hook.

Used by the Follow-up Sequences Engine to centralise compliance checks before any
real outbound send. If the Compliance Monitor is unavailable the client returns
`None` and the engine falls back to its local guardrails.
"""
from __future__ import annotations

import logging
from datetime import datetime

import httpx

from app.core.config import Settings
from app.domain.models import Contact

logger = logging.getLogger(__name__)


def _map_channel(channel: str) -> str:
    """Map FUS channel names to the CMS precheck channel vocabulary."""
    return "call" if channel == "call_task" else channel


def precheck(
    contact: Contact,
    channel: str,
    body: str,
    scheduled_for: datetime,
    *,
    settings: Settings,
) -> dict | None:
    """Call the Compliance Monitor ``POST /api/v1/precheck`` and return the JSON result.

    Returns ``None`` when the client is disabled or the call fails, allowing the
    engine to fall back to local guardrails.
    """
    if not settings.precheck_enabled or not settings.compliance_monitor_url:
        return None

    url = settings.compliance_monitor_url.rstrip("/") + "/api/v1/precheck"
    payload = {
        "channel": _map_channel(channel),
        "contact_id": contact.contact_id,
        "content": body,
        "sender_id": contact.agent_name,
        "has_unsubscribe": channel == "email" and "unsubscribe" in body.lower(),
        "scheduled_for": scheduled_for.isoformat(),
    }

    headers = {"Content-Type": "application/json"}
    if settings.compliance_api_key:
        headers["X-API-Key"] = settings.compliance_api_key
    elif settings.api_key:
        headers["X-API-Key"] = settings.api_key

    try:
        with httpx.Client(timeout=settings.precheck_timeout) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("compliance_precheck_failed", error=str(exc), url=url)
        return None
