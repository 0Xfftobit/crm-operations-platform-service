"""Regulatory-change monitoring (CEAAPSS-565).

The real system subscribes to official gazette / TRAI / RERA feeds or a legal-update API; that
external source is the pluggable seam. Here we implement the *reaction*: an ingested change flips
the tracked regulation to ``review_needed`` and (via the service) alerts the compliance/legal team,
and a staleness sweep flags regulations whose review is overdue. The subsequent rule update runs
through the two-step approval workflow in ``app.rules``.
"""
from datetime import datetime, timedelta

from app.domain.models import Regulation
from app.repositories import RegulationRepository


def report_change(
    repo: RegulationRepository, *, reg_id: str, now: datetime, summary: str = "",
) -> Regulation | None:
    """Record a detected external change → mark the regulation review_needed (None if untracked)."""
    reg = repo.get(reg_id)
    if reg is None:
        return None
    reg.status = "review_needed"
    reg.source_url = reg.source_url or summary
    repo.put(reg)
    return reg


def mark_reviewed(
    repo: RegulationRepository, *, reg_id: str, now: datetime,
) -> Regulation | None:
    reg = repo.get(reg_id)
    if reg is None:
        return None
    reg.status = "current"
    reg.last_reviewed = now
    repo.put(reg)
    return reg


def stale_sweep(repo: RegulationRepository, *, now: datetime, max_age_days: int = 180) -> list[str]:
    """Flag current regulations not reviewed within ``max_age_days`` as review_needed."""
    flagged: list[str] = []
    cutoff = now - timedelta(days=max_age_days)
    for reg in repo.list():
        if reg.status == "current" and (reg.last_reviewed is None or reg.last_reviewed < cutoff):
            reg.status = "review_needed"
            repo.put(reg)
            flagged.append(reg.reg_id)
    return flagged
