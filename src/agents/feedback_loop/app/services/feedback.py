"""Feedback-loop logic: score an outcome, aggregate per-engine quality + drift, decide on retraining."""
from __future__ import annotations

from uuid import uuid4

from app import store
from app.core.config import get_settings
from app.domain.models import (
    EngineMetrics,
    Outcome,
    OutcomeIn,
    RetrainRequest,
    RetrainRun,
)


def record_outcome(data: OutcomeIn) -> Outcome:
    s = get_settings()
    error = abs(data.prediction - data.outcome)
    outcome = Outcome(
        **data.model_dump(),
        outcome_id=f"oc-{uuid4().hex[:10]}",
        correct=error <= s.correct_tolerance,
        error=round(error, 6),
        reward=round(max(0.0, 1.0 - error), 6),
    )
    store.add_outcome(outcome)
    return outcome


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def engine_metrics(engine: str) -> EngineMetrics:
    s = get_settings()
    items = store.outcomes_for(engine)
    n = len(items)
    m = EngineMetrics(engine=engine, samples=n)
    if not items:
        m.reason = "no outcomes yet"
        return m

    m.accuracy = _mean([1.0 if o.correct else 0.0 for o in items])
    m.mean_abs_error = _mean([o.error for o in items])
    m.positive_rate = _mean([o.outcome for o in items])
    m.last_outcome_at = max(o.recorded_at for o in items)

    recent = items[-s.recent_window:]
    m.recent_accuracy = _mean([1.0 if o.correct else 0.0 for o in recent])
    if m.accuracy is not None and m.recent_accuracy is not None:
        m.drift = round(m.accuracy - m.recent_accuracy, 4)

    m.samples_since_retrain = n - store.baseline(engine)

    reasons = []
    if n >= s.min_samples and m.accuracy is not None and m.accuracy < s.min_accuracy:
        reasons.append(f"accuracy {m.accuracy} < {s.min_accuracy}")
    if m.drift is not None and m.drift > s.drift_threshold:
        reasons.append(f"drift {m.drift} > {s.drift_threshold}")
    if m.samples_since_retrain >= s.retrain_after_n:
        reasons.append(f"{m.samples_since_retrain} new outcomes since last retrain")
    m.needs_retrain = bool(reasons)
    m.reason = "; ".join(reasons) if reasons else "healthy"
    return m


def all_metrics() -> list[EngineMetrics]:
    return [engine_metrics(e) for e in store.engines()]


def trigger_retrain(req: RetrainRequest) -> RetrainRun:
    """Record a retraining run. The scaffold completes synchronously and resets the drift baseline;
    a production build hands the accumulated outcomes to the model trainer here."""
    metrics = engine_metrics(req.engine)
    run = RetrainRun(
        run_id=f"rt-{uuid4().hex[:10]}",
        engine=req.engine,
        status="completed",
        samples_used=metrics.samples,
        accuracy_at_trigger=metrics.accuracy,
        triggered_by=req.triggered_by,
        note=req.note or "scaffold: baseline reset; wire the real trainer here",
    )
    store.add_run(run)          # also resets samples_since_retrain for the engine
    return run
