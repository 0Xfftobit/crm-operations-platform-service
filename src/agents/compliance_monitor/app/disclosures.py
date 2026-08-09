"""Disclosure clocks — ECOA adverse-action + RERA/TILA-RESPA disclosure-at-pipeline-stage (FR-006).

Beyond the 30-day adverse-action notice, required disclosures are tracked per pipeline stage: when a
deal reaches a stage, the disclosures that stage mandates (and their deadlines) are started, and a
sweep warns/escalates as each nears or passes its due date. ``DISCLOSURE_REQUIREMENTS`` is the
default regulatory map (kind, due-days) by stage — the RERA/regulatory disclosure tracker (BR-5).
"""
from datetime import datetime, timedelta

from app.core.config import Settings
from app.domain.models import DisclosureTimer

# Required disclosures by pipeline stage: stage → [(kind, due_days)].
DISCLOSURE_REQUIREMENTS: dict[str, list[tuple[str, int]]] = {
    "application": [("loan_estimate", 3)],            # TILA-RESPA Loan Estimate
    "pre_closing": [("closing_disclosure", 3)],       # Closing Disclosure ≥3 days before closing
    "closing_scheduled": [("closing_disclosure", 3)],
    "denied": [("adverse_action", 30)],               # ECOA adverse-action notice
    "withdrawn": [("adverse_action", 30)],
    "counter_offer": [("adverse_action", 30)],
}


def requirements_for(stage: str) -> list[tuple[str, int]]:
    return DISCLOSURE_REQUIREMENTS.get((stage or "").lower(), [])


def start(deal_id: str, *, kind: str, due_days: int, now: datetime,
          stage: str = "") -> DisclosureTimer:
    return DisclosureTimer(deal_id=deal_id, kind=kind, stage=stage, started_at=now,
                           due_at=now + timedelta(days=due_days))


def start_adverse_action(deal_id: str, *, settings: Settings, now: datetime) -> DisclosureTimer:
    return start(deal_id, kind="adverse_action", due_days=settings.adverse_action_days, now=now,
                 stage="denied")


def status(timer: DisclosureTimer, *, settings: Settings, now: datetime) -> dict:
    """Return {state, days_left, warn}: satisfied | overdue(critical) | warning | ok."""
    if timer.satisfied:
        return {"state": "satisfied", "days_left": None, "warn": False}
    delta = timer.due_at - now
    days_left = delta.total_seconds() / 86400
    if now >= timer.due_at:
        return {"state": "overdue", "days_left": days_left, "warn": True}   # → critical
    offsets = sorted((int(o) for o in settings.disclosure_warn_offsets), reverse=True)
    if offsets and days_left <= offsets[0]:
        return {"state": "warning", "days_left": days_left, "warn": True}
    return {"state": "ok", "days_left": days_left, "warn": False}
