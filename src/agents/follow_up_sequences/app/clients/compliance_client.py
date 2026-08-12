"""Synchronous HTTP client to the Compliance Monitor pre-send hook.

Used by the Follow-up Sequences Engine to centralise compliance checks before any
real outbound send. If the Compliance Monitor is unavailable the client returns
``None`` and the engine falls back to its local guardrails.
"""
from __future__ import annotations

import logging
from datetime import datetime

from app.core.config import Settings
from app.core.resilience import CircuitBreakerOpen, ResilientClient
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

    Returns ``None`` when the client is disabled, the circuit breaker is open, or
    the call fails, allowing the engine to fall back to local guardrails.
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
        client = ResilientClient(client="compliance", timeout=settings.precheck_timeout)
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()
    except CircuitBreakerOpen:
        logger.warning("compliance_precheck_circuit_open: url=%s", url)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("compliance_precheck_failed: %s", exc)
        return None
