"""P2 — Anomaly detection on core KPIs and threshold alerts."""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import Any

from app import dashboard_builder, semantic
from app.core.config import get_settings
from app.domain.models import AlertRecord
from app.period_utils import _parse_grain
from app.repositories import get_alert_repository, get_event_log_repository


def _now() -> datetime:
    return datetime.now(UTC)


def _zscore(value: float, history: list[float]) -> float | None:
    if len(history) < 2:
        return None
    mean = statistics.mean(history)
    stdev = statistics.stdev(history)
    if stdev == 0:
        return 0.0
    return (value - mean) / stdev


def _metric_history(metric_name: str, period_grain: str = "monthly") -> dict[str, list[float]]:
    """Return historical values per dimension for a metric."""
    definition = semantic.get_metric_definition(metric_name)
    if definition is None:
        return {}
    # Compute the metric for all historical periods (no filter)
    result = dashboard_builder.compute_kpi(definition, period=period_grain, dims=["period"])
    by_key: dict[str, list[float]] = {}
    for row in result.get("rows", []):
        key = row.get("period", "all")
        if key not in by_key:
            by_key[key] = []
        by_key[key].append(float(row.get("value") or 0))
    return by_key


def detect_anomalies(metric_name: str = "app_to_fund_conversion", period: str | None = None) -> list[AlertRecord]:
    """Compare the current period to historical periods and flag z-score breaches."""
    settings = get_settings()
    definition = semantic.get_metric_definition(metric_name)
    if definition is None:
        return []

    grain = _parse_grain(period) or "monthly"
    current = dashboard_builder.compute_kpi(definition, period=period, dims=["period"])
    history = _metric_history(metric_name, period_grain=grain)
    alerts: list[AlertRecord] = []

    for row in current.get("rows", []):
        key = tuple(v for k, v in row.items() if k not in {"value"})
        hist = [v for k, values in history.items() if k != row.get("period", "") for v in values]
        if len(hist) < settings.anomaly_min_history:
            continue
        value = float(row.get("value") or 0)
        z = _zscore(value, hist)
        if z is None:
            continue
        if abs(z) > settings.anomaly_z_threshold:
            severity = "critical" if abs(z) > settings.anomaly_z_threshold * 1.5 else "warning"
            alert = AlertRecord(
                metric=metric_name,
                dimension=":".join(str(x) for x in key),
                value=round(value, 4),
                expected=round(statistics.mean(hist), 4),
                zscore=round(z, 4),
                severity=severity,
            )
            get_alert_repository().add(alert)
            get_event_log_repository().add(
                "analytics.alert",
                {
                    "metric": metric_name,
                    "dimension": alert.dimension,
                    "value": alert.value,
                    "zscore": alert.zscore,
                },
            )
            alerts.append(alert)
    return alerts


def threshold_alert(metric_name: str, threshold: float, direction: str = "below") -> dict[str, Any]:
    """Fire a threshold-based alert (e.g. conversion dropped below X%)."""
    definition = semantic.get_metric_definition(metric_name)
    if definition is None:
        return {"status": "unknown_metric"}
    result = dashboard_builder.compute_kpi(definition, period="monthly", dims=["period"])
    triggered = []
    for row in result.get("rows", []):
        value = float(row.get("value") or 0)
        breach = (direction == "below" and value < threshold) or (direction == "above" and value > threshold)
        if breach:
            alert = AlertRecord(
                metric=metric_name,
                dimension=":".join(str(v) for k, v in row.items() if k not in {"value"}),
                value=round(value, 4),
                expected=threshold,
                severity="critical",
            )
            get_alert_repository().add(alert)
            triggered.append(alert.model_dump(mode="json"))
    return {"metric": metric_name, "threshold": threshold, "direction": direction, "triggered": triggered}
