"""P1 — Labels: point-in-time joins between predictions and outcomes/feedback.

Implements immediate labels (FR-002), windowed/delayed labels (FR-003), and
point-in-time attribution (FR-006) using the prediction's ``features_ref``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import get_settings
from app.domain.models import Feedback, Label, Outcome, Prediction
from app.repositories import (
    get_event_log_repository,
    get_feedback_repository,
    get_label_repository,
    get_outcome_repository,
    get_prediction_repository,
)


def _now() -> datetime:
    return datetime.now(UTC)


def window_for_event(event_type: str, settings: Any | None = None) -> int | None:
    """Return the observation-window in days for a given outcome event."""
    if settings is None:
        settings = get_settings()
    stage_events = {"stage.progressed", "application.submitted", "stage_changed", "stage.progression"}
    response_events = {"email.replied", "message.replied", "replied", "sms.replied"}
    conversion_events = {"deal.funded", "funded", "converted", "conversion"}
    if event_type in response_events:
        return settings.window_response_days
    if event_type in stage_events:
        return settings.window_stage_days
    if event_type in conversion_events:
        return settings.funded_label_window_days
    # Default conversion window for other outcome events.
    return settings.window_conversion_days


def _label_value_for_outcome(outcome: Outcome) -> float | None:
    from app.collector import is_failure_event, is_success_event

    if is_success_event(outcome.event_type):
        return 1.0
    if is_failure_event(outcome.event_type):
        return 0.0
    return None


def _label_value_for_feedback(feedback: Feedback) -> Any:
    if feedback.label_value is not None:
        if isinstance(feedback.label_value, (int, float)):
            return float(feedback.label_value)
        if feedback.label_value in ("positive", "up", "yes"):
            return 1.0
        if feedback.label_value in ("negative", "down", "no"):
            return 0.0
        return feedback.label_value
    if feedback.feedback_type == "override" and feedback.chosen_action:
        return feedback.chosen_action
    return None


def _event_to_label_type(event_type: str) -> str:
    return event_type.split(".")[-1] if "." in event_type else event_type


def _existing_label_for_outcome(prediction_id: str, outcome_id: str) -> Label | None:
    for lab in get_label_repository().get_for_prediction(prediction_id):
        if lab.label_source == "outcome_event" and lab.label_value is not None:
            if lab.label_id == outcome_id:
                return lab
    return None


def _build_label(
    prediction: Prediction,
    source: str,
    label_type: str,
    label_value: Any,
    observed_at: datetime,
    in_window: bool,
    outcome_id: str | None = None,
    feedback_id: str | None = None,
) -> Label:
    return Label(
        prediction_id=prediction.prediction_id,
        entity_id=prediction.entity_id,
        engine_name=prediction.engine_name,
        label_type=label_type,
        label_value=label_value,
        label_source=source,
        observed_at=observed_at,
        in_window=in_window,
        features_ref=prediction.features_ref,
        outcome_id=outcome_id,
        feedback_id=feedback_id,
    )


def match_prediction_for_outcome(outcome: Outcome, settings: Any | None = None) -> Prediction | None:
    """Find the best matching prediction for an outcome by id or entity+window."""
    if outcome.prediction_id:
        return get_prediction_repository().get(outcome.prediction_id)

    if not outcome.entity_id:
        return None

    predictions = get_prediction_repository().list(engine_name=outcome.engine_name or None, entity_id=outcome.entity_id)
    if not predictions:
        return None

    if settings is None:
        settings = get_settings()
    window = window_for_event(outcome.event_type, settings)
    if window is None:
        window = settings.window_conversion_days

    best: Prediction | None = None
    for p in sorted(predictions, key=lambda x: x.scored_at, reverse=True):
        if p.scored_at <= outcome.occurred_at <= p.scored_at + timedelta(days=window):
            if best is None or p.scored_at > best.scored_at:
                best = p
    return best


def match_prediction_for_feedback(feedback: Feedback, settings: Any | None = None) -> Prediction | None:
    """Find the best matching prediction for a feedback/override record."""
    if feedback.prediction_id:
        return get_prediction_repository().get(feedback.prediction_id)

    if not feedback.entity_id:
        return None

    predictions = get_prediction_repository().list(
        engine_name=feedback.engine_name or None, entity_id=feedback.entity_id
    )
    if not predictions:
        return None

    if settings is None:
        settings = get_settings()
    # Feedback is anchored to a recent prediction (response window).
    window = settings.window_response_days
    best: Prediction | None = None
    for p in sorted(predictions, key=lambda x: x.scored_at, reverse=True):
        if p.scored_at <= feedback.occurred_at <= p.scored_at + timedelta(days=window):
            if best is None or p.scored_at > best.scored_at:
                best = p
    return best


def _match_outcome_out_of_window(outcome: Outcome, settings: Any | None = None) -> Prediction | None:
    """Fallback: match an outcome to the latest prediction before it (within a year)."""
    if not outcome.entity_id:
        return None
    predictions = get_prediction_repository().list(engine_name=outcome.engine_name or None, entity_id=outcome.entity_id)
    if not predictions:
        return None
    if settings is None:
        settings = get_settings()
    max_window = max(
        settings.window_conversion_days,
        settings.window_stage_days,
        settings.window_response_days,
        settings.funded_label_window_days,
        365,
    )
    best: Prediction | None = None
    for p in sorted(predictions, key=lambda x: x.scored_at, reverse=True):
        if p.scored_at <= outcome.occurred_at <= p.scored_at + timedelta(days=max_window):
            if best is None or p.scored_at > best.scored_at:
                best = p
    return best


def build_labels_for_outcome(outcome: Outcome) -> list[Label]:
    """Create labels for a single outcome event."""
    settings = get_settings()

    # Idempotent: do not re-label an outcome that has already been labeled.
    existing = next(
        (lab for lab in get_label_repository().list() if lab.outcome_id == outcome.outcome_id),
        None,
    )
    if existing is not None:
        return [existing]

    prediction = match_prediction_for_outcome(outcome, settings)
    in_window = True
    if prediction is None:
        prediction = _match_outcome_out_of_window(outcome, settings)
        in_window = False
    if prediction is None:
        get_event_log_repository().add(
            "label.quarantined",
            {"outcome_id": outcome.outcome_id, "reason": "orphan_outcome"},
        )
        return []

    label_value = _label_value_for_outcome(outcome)
    if label_value is None:
        return []

    if in_window:
        window = window_for_event(outcome.event_type, settings)
        if window is None:
            window = settings.window_conversion_days
        in_window = prediction.scored_at <= outcome.occurred_at <= prediction.scored_at + timedelta(days=window)

    label = _build_label(
        prediction,
        "outcome_event",
        _event_to_label_type(outcome.event_type),
        label_value,
        outcome.occurred_at,
        in_window,
        outcome_id=outcome.outcome_id,
    )
    get_label_repository().add(label)
    return [label]


def build_labels_for_feedback(feedback: Feedback) -> list[Label]:
    """Create labels for an override or explicit feedback record."""
    settings = get_settings()

    existing = next(
        (lab for lab in get_label_repository().list() if lab.feedback_id == feedback.feedback_id),
        None,
    )
    if existing is not None:
        return [existing]

    prediction = match_prediction_for_feedback(feedback, settings)
    if prediction is None:
        get_event_log_repository().add(
            "label.quarantined",
            {"feedback_id": feedback.feedback_id, "reason": "orphan_feedback"},
        )
        return []

    label_value = _label_value_for_feedback(feedback)
    if label_value is None:
        return []

    source = "human_override" if feedback.feedback_type == "override" else "explicit"
    label_type = "override" if feedback.feedback_type == "override" else "explicit"
    in_window = True  # Feedback is anchored to the prediction it references.

    label = _build_label(
        prediction,
        source,
        label_type,
        label_value,
        feedback.occurred_at,
        in_window,
        feedback_id=feedback.feedback_id,
    )
    get_label_repository().add(label)
    return [label]


def build_labels_for_prediction(prediction_id: str) -> list[Label]:
    """Backfill labels for any orphan outcomes/feedback that match a newly logged prediction."""
    prediction = get_prediction_repository().get(prediction_id)
    if prediction is None:
        return []

    built: list[Label] = []
    outcomes = get_outcome_repository().list(
        entity_id=prediction.entity_id, unlabeled=True, labels=get_label_repository()
    )
    for outcome in outcomes:
        if (outcome.prediction_id is None or outcome.prediction_id == prediction_id) and outcome.engine_name in (
            None,
            prediction.engine_name,
        ):
            match = match_prediction_for_outcome(outcome)
            if match and match.prediction_id == prediction_id:
                built.extend(build_labels_for_outcome(outcome))

    for feedback in get_feedback_repository().list():
        if feedback.prediction_id is None and feedback.entity_id == prediction.entity_id:
            match = match_prediction_for_feedback(feedback)
            if match and match.prediction_id == prediction_id:
                built.extend(build_labels_for_feedback(feedback))

    return built


def build_all_labels(now: datetime | None = None) -> list[Label]:
    """Batch reprocess all unlabeled outcomes and feedback."""
    built: list[Label] = []
    for outcome in get_outcome_repository().list(unlabeled=True, labels=get_label_repository()):
        built.extend(build_labels_for_outcome(outcome))
    for feedback in get_feedback_repository().list():
        # Only build if no label exists for this feedback-prediction pair.
        if feedback.prediction_id and not get_label_repository().get_for_prediction(feedback.prediction_id):
            built.extend(build_labels_for_feedback(feedback))
    return built
