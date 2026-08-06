"""P2 — Monitoring: per-engine performance and calibration metrics."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from sklearn.metrics import roc_auc_score

from app.domain.models import Prediction
from app.repositories import get_label_repository, get_prediction_repository


def _probability(prediction: Prediction) -> float:
    """Extract a scalar probability from the prediction output."""
    out = prediction.output or {}
    for key in ("probability", "p", "score", "confidence", "prob"):
        if key in out and isinstance(out[key], (int, float)):
            return float(out[key])
    # Fall back to 0.5 if no probability is present.
    return 0.5


def _to_binary(label_value: Any) -> float | None:
    if label_value is None:
        return None
    if isinstance(label_value, bool):
        return 1.0 if label_value else 0.0
    if isinstance(label_value, (int, float)):
        return float(label_value)
    if isinstance(label_value, str):
        if label_value.lower() in ("positive", "up", "yes", "true", "1", "success"):
            return 1.0
        if label_value.lower() in ("negative", "down", "no", "false", "0", "failure"):
            return 0.0
    return None


def _safe_auc(y_true: list[float], y_prob: list[float]) -> float | None:
    if len(set(y_true)) < 2:
        return None
    try:
        return float(roc_auc_score(y_true, y_prob))
    except Exception:
        return None


def _precision_recall_f1(y_true: list[float], y_pred: list[float]) -> tuple[float | None, float | None, float | None]:
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1.0 and p == 1.0)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0.0 and p == 1.0)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1.0 and p == 0.0)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t == 0.0 and p == 0.0)

    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and (precision + recall)
        else None
    )
    accuracy = (tp + tn) / len(y_true) if y_true else None
    return precision, recall, f1, accuracy  # type: ignore[return-value]


def _calibration_ece(y_true: list[float], y_prob: list[float], n_bins: int = 10) -> float | None:
    """Expected Calibration Error (ECE) with equal-width bins."""
    if not y_true or not y_prob:
        return None
    y_true = np.asarray(y_true, dtype=float)
    y_prob = np.asarray(y_prob, dtype=float)
    if len(y_true) < n_bins:
        return None
    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    total = len(y_true)
    for i in range(n_bins):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        if i == n_bins - 1:
            mask = (y_prob >= lo) & (y_prob <= hi)
        else:
            mask = (y_prob >= lo) & (y_prob < hi)
        count = mask.sum()
        if count == 0:
            continue
        avg_confidence = y_prob[mask].mean()
        avg_accuracy = y_true[mask].mean()
        ece += (count / total) * abs(avg_confidence - avg_accuracy)
    return float(ece)


def engine_metrics(
    engine_name: str,
    *,
    window_days: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compute precision, recall, F1, accuracy, AUC, and ECE for an engine (FR-009)."""
    now = now or datetime.now(UTC)

    labels = get_label_repository().list(engine_name=engine_name)
    if window_days is not None:
        cutoff = now - timedelta(days=window_days)
        labels = [lab for lab in labels if lab.observed_at >= cutoff]

    y_true: list[float] = []
    y_prob: list[float] = []
    y_pred: list[float] = []
    label_count = 0

    for lab in labels:
        if not lab.in_window:
            continue
        binary = _to_binary(lab.label_value)
        if binary is None:
            continue
        prediction = get_prediction_repository().get(lab.prediction_id)
        if prediction is None:
            continue
        prob = _probability(prediction)
        y_true.append(binary)
        y_prob.append(prob)
        y_pred.append(1.0 if prob >= 0.5 else 0.0)
        label_count += 1

    precision, recall, f1, accuracy = _precision_recall_f1(y_true, y_pred)
    auc = _safe_auc(y_true, y_prob)
    ece = _calibration_ece(y_true, y_prob)

    predictions = get_prediction_repository().list(engine_name=engine_name)
    match_rate = label_count / len(predictions) if predictions else 0.0

    return {
        "engine_name": engine_name,
        "support": label_count,
        "predictions": len(predictions),
        "match_rate": round(match_rate, 4),
        "accuracy": round(accuracy, 4) if accuracy is not None else None,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "auc": round(auc, 4) if auc is not None else None,
        "ece": round(ece, 4) if ece is not None else None,
    }


def all_engine_metrics(window_days: int | None = None, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Return metrics for every engine with at least one label."""
    by_engine: set[str] = set()
    for lab in get_label_repository().list():
        by_engine.add(lab.engine_name)
    return {engine: engine_metrics(engine, window_days=window_days, now=now) for engine in sorted(by_engine)}


def per_engine_label_counts() -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for lab in get_label_repository().list():
        counts[lab.engine_name] += 1
    return dict(counts)
