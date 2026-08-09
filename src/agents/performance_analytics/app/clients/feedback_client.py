"""Synchronous HTTP client to the Feedback Loop System.

PAS fans out ``loan.funded`` / ``deal.lost`` outcome events so FLS can build training
labels and compute model performance. Failures are logged and ignored — PAS never
blocks ingestion on an optional downstream fan-out.
"""
from __future__ import annotations

import logging
from uuid import uuid4

import httpx

from app.core.config import Settings
from app.domain.models import OutcomeEvent

logger = logging.getLogger(__name__)


def fanout_outcome(outcome: OutcomeEvent, *, settings: Settings) -> dict | None:
    """POST a PAS outcome to FLS ``POST /api/v1/outcomes``.

    Returns the FLS response, or ``None`` if disabled or the call fails.
    """
    if not settings.outcome_fanout_enabled or not settings.feedback_loop_url:
        return None

    url = settings.feedback_loop_url.rstrip("/") + "/api/v1/outcomes"
    payload = {
        "outcome_id": outcome.outcome_id,
        "entity_id": outcome.deal_id or outcome.contact_pid,
        "engine_name": "lead_scoring",
        "event_type": outcome.event_type,
        "metric_value": outcome.amount,
        "occurred_at": outcome.occurred_at.isoformat(),
        "prediction_id": None,
    }
    headers = {"Content-Type": "application/json", "X-Request-ID": uuid4().hex}
    api_key = settings.feedback_loop_api_key or settings.api_key
    if api_key:
        headers["X-API-Key"] = api_key

    try:
        with httpx.Client(timeout=settings.feedback_loop_timeout) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("feedback_loop_fanout_failed", error=str(exc), url=url)
        return None
