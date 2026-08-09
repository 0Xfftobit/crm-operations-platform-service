"""P2 — Drift monitoring: Population Stability Index (PSI) per feature."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any

import numpy as np

from app.core.config import get_settings
from app.domain.models import DriftRecord
from app.repositories import get_drift_repository, get_prediction_repository

# Engine -> feature -> list of reference values
_reference_distributions: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
_lock = RLock()


def reset_drift_references() -> None:
    with _lock:
        _reference_distributions.clear()


def set_reference(engine_name: str, feature: str, values: list[float]) -> None:
    """Set the reference distribution for a feature (typically from training data)."""
    with _lock:
        _reference_distributions[engine_name][feature] = [float(v) for v in values]


def set_reference_from_predictions(
    engine_name: str,
    predictions: list[Any],
    features: list[str] | None = None,
) -> None:
    """Bootstrap a reference distribution from the features of the first predictions."""
    if features is None:
        features = _discover_features(predictions)
    with _lock:
        for feature in features:
            values = [
                float(p.features[feature])
                for p in predictions
                if feature in p.features and _is_number(p.features[feature])
            ]
            if values:
                set_reference(engine_name, feature, values)


def get_reference(engine_name: str, feature: str) -> list[float]:
    with _lock:
        return _reference_distributions.get(engine_name, {}).get(feature, [])


def _is_number(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _discover_features(predictions: list[Any]) -> list[str]:
    features: set[str] = set()
    for p in predictions:
        for k, v in p.features.items():
            if _is_number(v):
                features.add(k)
    return sorted(features)


def psi(reference: list[float], current: list[float], n_bins: int = 10) -> float:
    """Population Stability Index between two distributions."""
    if not reference or not current:
        return 0.0

    ref = np.asarray(reference, dtype=float)
    cur = np.asarray(current, dtype=float)

    min_v = min(float(ref.min()), float(cur.min()))
    max_v = max(float(ref.max()), float(cur.max()))
    if min_v == max_v:
        return 0.0

    edges = np.linspace(min_v, max_v, n_bins + 1)
    ref_hist, _ = np.histogram(ref, bins=edges)
    cur_hist, _ = np.histogram(cur, bins=edges)

    # Add a small epsilon to avoid division-by-zero / log-of-zero.
    ref_pct = (ref_hist + 1e-4) / (ref_hist.sum() + 1e-4 * n_bins)
    cur_pct = (cur_hist + 1e-4) / (cur_hist.sum() + 1e-4 * n_bins)

    return float(np.sum((ref_pct - cur_pct) * np.log(ref_pct / cur_pct)))


def _recent_values(engine_name: str, feature: str, max_age_days: int = 30) -> list[float]:
    now = datetime.now(UTC)
    values: list[float] = []
    cutoff = timedelta(days=max_age_days)
    for p in get_prediction_repository().list(engine_name=engine_name):
        if now - p.scored_at <= cutoff and feature in p.features and _is_number(p.features[feature]):
            values.append(float(p.features[feature]))
    return values


def _consecutive_alerts(engine_name: str, feature: str) -> int:
    records = sorted(
        get_drift_repository().list(engine_name=engine_name, feature=feature),
        key=lambda r: r.evaluated_at,
    )
    count = 0
    for r in reversed(records):
        if r.alert:
            count += 1
        else:
            break
    return count


def drift_evaluate(
    engine_name: str,
    feature: str,
    current_values: list[float] | None = None,
    settings: Any | None = None,
) -> DriftRecord:
    """Evaluate PSI for one feature against its reference distribution."""
    if settings is None:
        settings = get_settings()

    if current_values is None:
        current_values = _recent_values(engine_name, feature)

    reference = get_reference(engine_name, feature)
    psi_value = psi(reference, current_values) if reference else 0.0
    over_threshold = psi_value > settings.psi_threshold
    # `alert` marks a threshold breach for this record. Sustained retrain
    # triggering is handled by `should_trigger_retrain` using consecutive alerts.
    alert = over_threshold

    record = DriftRecord(
        engine_name=engine_name,
        feature=feature,
        psi=round(psi_value, 4),
        reference_window=f"n={len(reference)}",
        alert=alert,
    )
    get_drift_repository().add(record)
    return record


def drift_evaluate_all(engine_name: str, settings: Any | None = None) -> list[DriftRecord]:
    """Evaluate PSI for every numeric feature seen in recent predictions."""
    if settings is None:
        settings = get_settings()

    predictions = get_prediction_repository().list(engine_name=engine_name)
    features = _discover_features(predictions)
    records: list[DriftRecord] = []
    for feature in features:
        current = _recent_values(engine_name, feature)
        records.append(drift_evaluate(engine_name, feature, current, settings))
    return records


def should_trigger_retrain(engine_name: str, settings: Any | None = None) -> tuple[bool, list[str]]:
    """Return (trigger, [feature names]) if sustained drift is detected."""
    if settings is None:
        settings = get_settings()

    triggered: list[str] = []
    seen: set[str] = set()
    # Sustained drift is declared when any feature has `drift_persistence_evals` consecutive alerts.
    for record in get_drift_repository().list(engine_name=engine_name):
        if record.alert and record.feature not in seen:
            consecutive = _consecutive_alerts(engine_name, record.feature)
            # Current record is already included as the latest alert, so consecutive >= persistence.
            if consecutive >= settings.drift_persistence_evals:
                triggered.append(record.feature)
                seen.add(record.feature)
    return bool(triggered), triggered
