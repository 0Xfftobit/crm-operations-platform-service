"""A/B variant allocation (FR: A/B testing) — deterministic, balanced step-content assignment.

When a step defines content ``variants``, each enrollment is assigned one deterministically by a
of its id, so the split is stable across restarts (the same enrollment always gets the same variant)
and roughly balanced. Outcome attribution reads the assigned variant off the ledger/enrollment.
"""
from __future__ import annotations

import hashlib

from app.domain.models import Step


def allocate(step: Step, enrollment_id: str) -> tuple[str, str]:
    """Return (variant_name, template) for the enrollment's step; no variants → base template."""
    if not step.variants:
        return "", step.template
    names = sorted(step.variants)
    idx = int(hashlib.md5(f"{step.id}:{enrollment_id}".encode()).hexdigest(), 16) % len(names)
    name = names[idx]
    return name, step.variants[name]
