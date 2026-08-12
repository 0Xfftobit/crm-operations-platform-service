"""Synchronous HTTP client to the Follow-up Sequences Engine.

Task Automation fans out ``deal.stage_changed`` and ``lead.assigned`` events to FUS
so it can auto-enroll contacts and advance/stop sequences. Failures are logged and
ignored; TAS does not block its own processing on an optional cross-agent fan-out.
"""
from __future__ import annotations

import logging
from uuid import uuid4

from app.core.config import Settings
from app.core.resilience import ResilientClient

logger = logging.getLogger(__name__)

_resilient = ResilientClient(client_label="followup")


def _api_key(settings: Settings) -> str:
    return settings.follow_up_sequences_api_key or settings.api_key


def _headers(settings: Settings) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "X-Request-ID": uuid4().hex}
    key = _api_key(settings)
    if key:
        headers["X-API-Key"] = key
    return headers


def _maybe_log_failure(lead_id: str, error: Exception | str, stage: str | None = None, score: float | None = None) -> None:
    if score is not None:
        logger.warning("fus_lead_scored_failed", extra={"error": str(error), "lead": lead_id, "score": score})
    else:
        logger.warning("fus_stage_changed_failed", extra={"error": str(error), "lead": lead_id, "stage": stage})


def stage_changed(lead_id: str, stage: str, *, tenant: str, settings: Settings) -> dict | None:
    """Notify FUS of a stage change so it can exit sequences on conversion / loss."""
    if not settings.fus_fanout_enabled or not settings.follow_up_sequences_url:
        return None
    url = settings.follow_up_sequences_url.rstrip("/") + "/api/v1/stage-changed"
    payload = {"contact_id": lead_id, "stage": stage, "tenant_id": tenant}
    result = _resilient.request(
        "POST",
        url,
        headers=_headers(settings),
        json=payload,
        timeout=settings.integration_timeout_s,
        fallback=None,
    )
    if result is None:
        _maybe_log_failure(lead_id, "fan-out failed or circuit open", stage=stage)
    return result


def lead_scored(lead_id: str, score: float, sequence_id: str, *, tenant: str,
                context: dict | None, settings: Settings) -> dict | None:
    """Notify FUS that a lead was scored so it can auto-enroll above the threshold."""
    if not settings.fus_fanout_enabled or not settings.follow_up_sequences_url:
        return None
    url = settings.follow_up_sequences_url.rstrip("/") + "/api/v1/triggers/lead-scored"
    payload = {
        "contact_id": lead_id,
        "score": score,
        "sequence_id": sequence_id,
        "tenant_id": tenant,
        "context": context or {},
    }
    result = _resilient.request(
        "POST",
        url,
        headers=_headers(settings),
        json=payload,
        timeout=settings.integration_timeout_s,
        fallback=None,
    )
    if result is None:
        _maybe_log_failure(lead_id, "fan-out failed or circuit open", score=score)
    return result
