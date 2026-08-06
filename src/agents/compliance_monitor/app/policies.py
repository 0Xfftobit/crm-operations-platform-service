"""Engine-8 policy ingestion (FR-014 / edge: 'conflicting policy version → use latest').

Engine 8 pushes signed, versioned policy thresholds; the highest version wins. Known knobs are
mapped onto the runtime ``Settings`` so the detectors transparently use the central policy without
any per-call plumbing. Unknown keys are ignored (forward-compatible).
"""
from app.core.config import Settings
from app.domain.models import CompliancePolicy

# Policy threshold keys we honour → Settings field names (identity map, listed for allow-listing).
_KNOWN = {
    "fairness_ratio_min": "fairness_ratio_min",
    "fairness_ratio_max": "fairness_ratio_max",
    "disparity_min_sample": "disparity_min_sample",
    "adverse_action_days": "adverse_action_days",
    "quiet_hours_start": "quiet_hours_start",
    "quiet_hours_end": "quiet_hours_end",
    "precheck_latency_budget_ms": "precheck_latency_budget_ms",
}


def effective_settings(base: Settings, policy: CompliancePolicy | None) -> Settings:
    """Overlay an active policy's thresholds onto the base settings (no policy → base unchanged)."""
    if policy is None:
        return base
    overrides = {_KNOWN[k]: v for k, v in policy.thresholds.items() if k in _KNOWN}
    return base.model_copy(update=overrides) if overrides else base
