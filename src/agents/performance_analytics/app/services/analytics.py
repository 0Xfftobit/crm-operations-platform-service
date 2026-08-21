"""KPI rollups + z-score anomaly detection (pure-Python stats; no numpy dependency)."""
from __future__ import annotations

from math import sqrt
from uuid import uuid4

from app import store
from app.core.config import get_settings
from app.domain.models import Anomaly, KpiRollup, MetricIn, MetricPoint


def record_metric(data: MetricIn) -> MetricPoint:
    point = MetricPoint(**data.model_dump(), point_id=f"pt-{uuid4().hex[:10]}")
    store.add_point(point)
    return point


def _pct(sorted_vals: list[float], p: float) -> float:
    """Linear-interpolated percentile (p in [0,1]) over a pre-sorted list."""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _mean_std(vals: list[float]) -> tuple[float, float]:
    n = len(vals)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n      # population variance
    return mean, sqrt(var)


def _rollup(metric: str, points: list, group: str | None = None) -> KpiRollup:
    vals = sorted(p.value for p in points)
    if not vals:
        return KpiRollup(metric=metric, group=group)
    mean, std = _mean_std(vals)
    latest = max(points, key=lambda p: p.recorded_at)
    return KpiRollup(
        metric=metric, group=group, count=len(vals), sum=round(sum(vals), 6),
        mean=round(mean, 6), min=vals[0], max=vals[-1],
        p50=round(_pct(vals, 0.50), 6), p95=round(_pct(vals, 0.95), 6),
        stddev=round(std, 6), last=latest.value, last_at=latest.recorded_at)


def rollup(metric: str, group_by: str | None = None) -> list[KpiRollup]:
    pts = store.points_for(metric, get_settings().window)
    if not group_by:
        return [_rollup(metric, pts)]
    groups: dict[str, list] = {}
    for p in pts:
        groups.setdefault(p.dimensions.get(group_by, "∅"), []).append(p)
    return [_rollup(metric, gp, group=g) for g, gp in sorted(groups.items())]


def all_kpis() -> list[KpiRollup]:
    return [_rollup(m, store.points_for(m, get_settings().window)) for m in store.metrics()]


def anomalies(metric: str, threshold: float | None = None) -> list[Anomaly]:
    s = get_settings()
    thr = s.z_threshold if threshold is None else threshold
    pts = store.points_for(metric, s.window)
    if len(pts) < s.min_samples:
        return []
    mean, std = _mean_std([p.value for p in pts])
    if std == 0:
        return []
    out = []
    for p in pts:
        z = (p.value - mean) / std
        if abs(z) > thr:
            out.append(Anomaly(metric=metric, point_id=p.point_id, value=p.value,
                               z_score=round(z, 4), mean=round(mean, 6), stddev=round(std, 6),
                               dimensions=p.dimensions, recorded_at=p.recorded_at))
    return out


def all_anomalies(threshold: float | None = None) -> list[Anomaly]:
    out: list[Anomaly] = []
    for m in store.metrics():
        out.extend(anomalies(m, threshold))
    return out
