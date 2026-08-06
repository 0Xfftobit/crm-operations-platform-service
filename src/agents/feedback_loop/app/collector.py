"""P0 — Capture: prediction, outcome, override, and explicit-feedback ingestion.

Every captured record is durably logged to the in-memory repository and, where a
stable ``prediction_id`` is present, immediately passed to the label builder.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app import label_builder
from app.domain.models import Feedback, Outcome, Prediction
from app.repositories import (
    get_event_log_repository,
    get_feedback_repository,
    get_outcome_repository,
    get_prediction_repository,
)

SUCCESS_EVENTS = {
    "deal.funded",
    "funded",
    "converted",
    "email.replied",
    "stage.progressed",
    "application.submitted",
    "approved",
}

FAILURE_EVENTS = {
    "deal.lost",
    "lost",
    "fell_out",
    "email.not_replied",
    "no_reply",
    "unsubscribed",
    "rejected",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _emit(event_type: str, payload: dict[str, Any]) -> None:
    get_event_log_repository().add(event_type, payload)


def log_prediction(prediction: Prediction) -> dict[str, Any]:
    """Ingest a prediction event (FR-001)."""
    repo = get_prediction_repository()
    existing = repo.get(prediction.prediction_id)
    if existing is not None:
        return {"status": "duplicate", "prediction_id": prediction.prediction_id}
    repo.put(prediction)
    _emit("feedback.captured", {"type": "prediction", "prediction_id": prediction.prediction_id})
    # Try to match any orphan outcomes that arrived before this prediction.
    label_builder.build_labels_for_prediction(prediction.prediction_id)
    return {"status": "logged", "prediction_id": prediction.prediction_id}


def _label_value_from_outcome(outcome: Outcome) -> Any:
    if outcome.event_type in SUCCESS_EVENTS:
        return 1.0
    if outcome.event_type in FAILURE_EVENTS:
        return 0.0
    # Unknown outcome types are recorded but not auto-labeled.
    return None


def record_outcome(outcome: Outcome) -> dict[str, Any]:
    """Ingest an outcome event (FR-002, FR-003)."""
    get_outcome_repository().add(outcome)
    _emit("feedback.captured", {"type": "outcome", "outcome_id": outcome.outcome_id})
    labels = label_builder.build_labels_for_outcome(outcome)
    return {"status": "recorded", "outcome_id": outcome.outcome_id, "labels": [label.label_id for label in labels]}


def record_override(override: Feedback) -> dict[str, Any]:
    """Ingest a human override (FR-004)."""
    override.feedback_type = "override"
    get_feedback_repository().add(override)
    _emit("feedback.captured", {"type": "override", "feedback_id": override.feedback_id})
    labels = label_builder.build_labels_for_feedback(override)
    return {"status": "recorded", "feedback_id": override.feedback_id, "labels": [label.label_id for label in labels]}


def record_explicit_feedback(feedback: Feedback) -> dict[str, Any]:
    """Ingest explicit user feedback such as thumbs up/down (FR-005)."""
    feedback.feedback_type = "explicit"
    get_feedback_repository().add(feedback)
    _emit("feedback.captured", {"type": "explicit", "feedback_id": feedback.feedback_id})
    labels = label_builder.build_labels_for_feedback(feedback)
    return {"status": "recorded", "feedback_id": feedback.feedback_id, "labels": [label.label_id for label in labels]}


def is_success_event(event_type: str) -> bool:
    return event_type in SUCCESS_EVENTS


def is_failure_event(event_type: str) -> bool:
    return event_type in FAILURE_EVENTS
