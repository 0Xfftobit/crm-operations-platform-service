"""A/B testing framework: consistent allocation, power analysis, significance, and early stopping."""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.models import Experiment, ExperimentVariant
from app.repositories import get_experiment_repository

# Standard normal quantiles for power analysis.
_Z_80 = 0.84  # one-sided 80% power
_Z_95 = 1.96  # two-sided 95% (alpha=0.05)


def _variant_hash(experiment_id: str, entity_id: str) -> int:
    """Deterministic 0-99 bucket from experiment + entity."""
    h = hashlib.md5(f"{experiment_id}:{entity_id}".encode()).hexdigest()
    return int(h, 16) % 100


def _normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _proportions_z_test(n1: int, x1: int, n2: int, x2: int) -> float:
    """Two-proportion two-sided z-test p-value."""
    if n1 == 0 or n2 == 0:
        return 1.0
    p1 = x1 / n1
    p2 = x2 / n2
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = (p1 - p2) / se
    return 2 * (1 - _normal_cdf(abs(z)))


def _min_sample_size_per_variant(baseline_rate: float, mde: float = 0.05) -> int:
    """Sample size per variant for a two-proportion test with 80% power and alpha=0.05."""
    if baseline_rate <= 0 or baseline_rate >= 1 or mde <= 0:
        return 0
    p1 = baseline_rate
    p2 = baseline_rate + mde
    p2 = max(0.001, min(0.999, p2))
    se_pooled = math.sqrt(p1 * (1 - p1)) + math.sqrt(p2 * (1 - p2))
    if se_pooled == 0:
        return 0
    n = ((_Z_95 + _Z_80) ** 2 * (se_pooled**2)) / (mde**2)
    return max(int(math.ceil(n)), 1)


def create_experiment(
    name: str,
    hypothesis: str,
    engine_name: str,
    variants: list[dict[str, Any]],
    traffic_split: dict[str, float] | None = None,
    audience_segment: str | None = None,
    experiment_type: str | None = None,
    success_metric: str = "conversion",
    guardrail_metric: str | None = None,
    guardrail_threshold: float | None = None,
    p_value_threshold: float = 0.05,
    min_duration_hours: float = 24.0,
) -> Experiment:
    """Create a new A/B/C experiment (CEAAPSS-560)."""
    parsed_variants = [ExperimentVariant(**v) for v in variants]
    if traffic_split is None:
        total = len(parsed_variants)
        traffic_split = {v.name: round(100 / total, 2) for v in parsed_variants}
    exp = Experiment(
        name=name,
        hypothesis=hypothesis,
        engine_name=engine_name,
        experiment_type=experiment_type,
        variants=parsed_variants,
        traffic_split=traffic_split,
        audience_segment=audience_segment,
        success_metric=success_metric,
        guardrail_metric=guardrail_metric,
        guardrail_threshold=guardrail_threshold,
        p_value_threshold=p_value_threshold,
        min_duration_hours=min_duration_hours,
    )
    get_experiment_repository().put(exp)
    return exp


def assign_variant(experiment_id: str, entity_id: str) -> str:
    """Return the variant name for an entity using consistent hashing."""
    exp = get_experiment_repository().get(experiment_id)
    if exp is None:
        raise ValueError(f"unknown experiment {experiment_id}")

    bucket = _variant_hash(experiment_id, entity_id)
    cumulative = 0
    for v in exp.variants:
        cumulative += exp.traffic_split.get(v.name, 0)
        if bucket < cumulative:
            return v.name
    return exp.variants[-1].name


def record_result(
    experiment_id: str,
    variant: str,
    entity_id: str,
    metric_value: float,
    guardrail_value: float | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Record a single result for an experiment variant."""
    exp = get_experiment_repository().get(experiment_id)
    if exp is None:
        raise ValueError(f"unknown experiment {experiment_id}")
    if exp.status not in ("running",):
        return {"status": "experiment_not_running"}

    if "results" not in exp.results:
        exp.results["results"] = []
    entry: dict[str, Any] = {
        "variant": variant,
        "entity_id": entity_id,
        "metric_value": float(metric_value),
        "timestamp": (timestamp or datetime.now(UTC)).isoformat(),
    }
    if guardrail_value is not None:
        entry["guardrail_value"] = float(guardrail_value)
    exp.results["results"].append(entry)
    get_experiment_repository().put(exp)
    return {"status": "recorded"}


def evaluate_experiment(experiment_id: str) -> dict[str, Any]:
    """Compute per-variant conversion rates, sample size, power-analysis gating, and significance."""
    exp = get_experiment_repository().get(experiment_id)
    if exp is None:
        raise ValueError(f"unknown experiment {experiment_id}")

    results = exp.results.get("results", [])
    by_variant: dict[str, dict[str, Any]] = {}
    for r in results:
        v = r["variant"]
        if v not in by_variant:
            by_variant[v] = {"n": 0, "successes": 0, "guardrail_sum": 0.0, "guardrail_n": 0, "values": []}
        by_variant[v]["n"] += 1
        value = float(r["metric_value"])
        by_variant[v]["values"].append(value)
        if value > 0:
            by_variant[v]["successes"] += 1
        if "guardrail_value" in r:
            by_variant[v]["guardrail_sum"] += float(r["guardrail_value"])
            by_variant[v]["guardrail_n"] += 1

    control = exp.variants[0].name if exp.variants else None
    report: dict[str, Any] = {"variants": {}}
    for v in exp.variants:
        stats = by_variant.get(v.name, {"n": 0, "successes": 0, "guardrail_sum": 0.0, "guardrail_n": 0, "values": []})
        n = stats["n"]
        successes = stats["successes"]
        rate = round(successes / n, 4) if n else 0.0
        guardrail_mean = round(stats["guardrail_sum"] / stats["guardrail_n"], 4) if stats["guardrail_n"] else None
        report["variants"][v.name] = {
            "n": n,
            "successes": successes,
            "conversion_rate": rate,
            "mean_metric": round(sum(stats["values"]) / n, 4) if n else 0.0,
            "guardrail_mean": guardrail_mean,
        }

    elapsed_hours = (datetime.now(UTC) - exp.started_at) / timedelta(hours=1)
    report["elapsed_hours"] = round(elapsed_hours, 2)
    report["min_duration_met"] = elapsed_hours >= exp.min_duration_hours

    if control and len(exp.variants) > 1:
        control_stats = by_variant.get(control, {"n": 0, "successes": 0})
        control_rate = report["variants"][control]["conversion_rate"]
        report["min_sample_size_per_variant"] = _min_sample_size_per_variant(control_rate or 0.1, mde=0.05)

        for v in exp.variants[1:]:
            challenger = by_variant.get(v.name, {"n": 0, "successes": 0})
            challenger_rate = report["variants"][v.name]["conversion_rate"]
            p_value = _proportions_z_test(
                control_stats["n"],
                control_stats["successes"],
                challenger["n"],
                challenger["successes"],
            )
            variant_report = report["variants"][v.name]
            variant_report["p_value"] = round(p_value, 4)

            # Power analysis uses the observed effect size between control and challenger.
            observed_mde = max(0.05, abs(control_rate - challenger_rate))
            min_sample = _min_sample_size_per_variant(control_rate or challenger_rate, mde=observed_mde)
            meets_size = challenger["n"] >= min_sample and control_stats["n"] >= min_sample
            meets_duration = report["min_duration_met"]
            meets_p = p_value < exp.p_value_threshold
            variant_report["significant"] = bool(meets_size and meets_duration and meets_p)
            variant_report["min_sample_size"] = min_sample
            variant_report["significance_reason"] = {
                "sample_size_ok": meets_size,
                "duration_ok": meets_duration,
                "p_value_ok": meets_p,
            }
    else:
        report["min_sample_size_per_variant"] = 0

    # Early stopping guardrail.
    guardrail_breached = False
    if exp.guardrail_metric:
        for v in exp.variants:
            guardrail_mean = report["variants"][v.name]["guardrail_mean"]
            if guardrail_mean is not None and exp.guardrail_threshold is not None:
                if guardrail_mean < exp.guardrail_threshold:
                    guardrail_breached = True
                    break

    if not guardrail_breached and exp.guardrail_threshold is not None:
        # Fallback: treat primary conversion rate as guardrail if no separate guardrail metric.
        rates = [vr["conversion_rate"] for vr in report["variants"].values()]
        if rates and min(rates) < exp.guardrail_threshold:
            guardrail_breached = True

    report["guardrail_breached"] = guardrail_breached
    report["p_value_threshold"] = exp.p_value_threshold
    report["status"] = exp.status
    return report


def promote_winner(experiment_id: str, *, force: bool = False) -> dict[str, Any]:
    """Promote the winning variant's model to 100% traffic."""
    exp = get_experiment_repository().get(experiment_id)
    if exp is None:
        raise ValueError(f"unknown experiment {experiment_id}")

    report = evaluate_experiment(experiment_id)
    if report["guardrail_breached"] and not force:
        return {"status": "guardrail_breached", "experiment_id": experiment_id, "report": report}

    winner = max(report["variants"], key=lambda v: report["variants"][v]["conversion_rate"])
    winner_significant = report["variants"].get(winner, {}).get("significant", False)
    if not winner_significant and not force:
        return {"status": "not_significant", "experiment_id": experiment_id, "winner": winner, "report": report}

    exp.status = "completed"
    exp.ended_at = datetime.now(UTC)
    exp.results["winner"] = winner
    get_experiment_repository().put(exp)

    variant = next((v for v in exp.variants if v.name == winner), None)
    if variant and variant.model_version:
        from app import model_retrainer

        model_retrainer.promote_model(variant.model_version)
    return {"status": "promoted", "winner": winner, "experiment_id": experiment_id, "report": report}
