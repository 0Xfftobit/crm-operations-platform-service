"""Batch disparity analytics — the pure aggregation the Spark job runs at scale (FR-015, design §5).

Kept free of Spark and app config so it is unit-testable and can execute inside a Spark executor: it
turns per-(sub_agent, cohort_dimension, cohort) selection counts into the 4/5ths verdict per group,
flagging geographic (redlining-style) disparities. The Spark runner in ``jobs/disparity_batch.py``
does the distributed group-by and calls this on the small aggregate.
"""
_GEO_TOKENS = ("geo", "zip", "tract", "region", "county", "district")


def disparity_from_counts(
    counts: list[dict], *, min_sample: int = 100, ratio_min: float = 0.80, ratio_max: float = 1.25,
) -> list[dict]:
    """counts = [{sub_agent, cohort_dimension, cohort, selected, total}] → one verdict per group."""
    groups: dict[tuple[str, str], dict[str, dict]] = {}
    for c in counts:
        key = (c["sub_agent"], c["cohort_dimension"])
        groups.setdefault(key, {})[c["cohort"]] = {"selected": c["selected"], "total": c["total"]}

    results: list[dict] = []
    for (sub, dim), cohorts in sorted(groups.items()):
        rates = {k: (v["selected"] / v["total"] if v["total"] else 0.0)
                 for k, v in cohorts.items()}
        positive = [r for r in rates.values() if r > 0]
        top = max(rates.values()) if rates else 0.0
        ratio = (min(positive) / top) if positive and top > 0 else 1.0
        min_ok = all(v["total"] >= min_sample for v in cohorts.values())
        passes = ratio_min <= ratio <= ratio_max
        geo = any(tok in dim.lower() for tok in _GEO_TOKENS)
        results.append({
            "sub_agent": sub, "cohort_dimension": dim,
            "selection_rates": {k: round(v, 4) for k, v in rates.items()},
            "ratio": round(ratio, 4), "passes": passes, "min_sample_met": min_ok,
            "redlining_risk": bool(geo and not passes and min_ok)})
    return results
