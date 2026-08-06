"""Sequence analytics (FR: sequence analytics; AC: delivery/open/reply rates per step).

Rolls the outreach ledger + engagement events into per-step and per-sequence rates: delivery rate
(delivered ÷ sent), open rate (opened ÷ delivered), reply rate (replied ÷ delivered). Powers the
manager dashboards and the "which sequences/messages perform best" question.
"""
from __future__ import annotations

from app.repositories import (
    get_engagement_repository,
    get_enrollment_repository,
    get_ledger_repository,
)


def sequence_analytics(sequence_id: str, *, tenant: str = "default") -> dict:
    """Per-step delivery/open/reply rates for a sequence, aggregated across its enrollments."""
    enr = get_enrollment_repository()
    eng = get_engagement_repository()
    # enrollment_ids belonging to this sequence (tenant-scoped)
    enroll_ids = {e.enrollment_id for e in enr.list(tenant) if e.sequence_id == sequence_id}
    steps: dict[str, dict] = {}
    for r in get_ledger_repository().list(tenant):
        if r.enrollment_id not in enroll_ids or r.status != "sent":
            continue
        s = steps.setdefault(r.step_id, {"sent": 0, "delivered": 0, "opened": 0, "replied": 0})
        s["sent"] += 1
        kinds = eng.kinds_for_step(r.enrollment_id, r.step_id)
        s["delivered"] += int("delivered" in kinds or bool(kinds))   # delivered or any engagement
        s["opened"] += int("open" in kinds)
        s["replied"] += int("reply" in kinds)

    def rate(n: int, d: int) -> float:
        return round(n / d, 4) if d else 0.0

    out = []
    for step_id, s in steps.items():
        out.append({
            "step_id": step_id, **s,
            "delivery_rate": rate(s["delivered"], s["sent"]),
            "open_rate": rate(s["opened"], s["delivered"]),
            "reply_rate": rate(s["replied"], s["delivered"]),
        })
    total_sent = sum(s["sent"] for s in steps.values())
    return {"sequence_id": sequence_id, "enrollments": len(enroll_ids),
            "total_sent": total_sent, "steps": out}
