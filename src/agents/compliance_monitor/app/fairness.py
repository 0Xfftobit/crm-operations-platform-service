"""Fair-lending disparity monitor — the 4/5ths rule (FR-005 / US-2 / design §4).

Given per-cohort ``{selected, total}`` for a decisioning sub-agent, compute each cohort's selection
rate and the disparity **ratio = min_rate / max_rate**. Passes when the ratio is inside the
configured band (default ≥ 0.80). A minimum-sample guard suppresses noisy small-cohort alerts.
Protected-class proxies are used **only** here for monitoring — never as a decision input (BR-2).
"""
from app.core.config import Settings
from app.domain.models import DisparityResult


def disparity(
    sub_agent: str, cohort_dimension: str, cohorts: dict[str, dict], *,
    settings: Settings, window: str = "",
) -> DisparityResult:
    """cohorts = {cohort_name: {"selected": int, "total": int}}."""
    rates = {name: (c["selected"] / c["total"] if c.get("total") else 0.0)
             for name, c in cohorts.items()}
    positive = [r for r in rates.values() if r > 0]
    ratio = (min(positive) / max(rates.values())) if positive and max(rates.values()) > 0 else 1.0
    min_sample_met = all(
        c.get("total", 0) >= settings.disparity_min_sample for c in cohorts.values())
    passes = settings.fairness_ratio_min <= ratio <= settings.fairness_ratio_max
    return DisparityResult(
        sub_agent=sub_agent, cohort_dimension=cohort_dimension,
        selection_rates={k: round(v, 4) for k, v in rates.items()},
        ratio=round(ratio, 4), passes=passes, min_sample_met=min_sample_met, window=window)
