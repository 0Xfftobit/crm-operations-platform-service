"""Tiered alerting + case management (FR-008/009 / design §5).

Detectors raise ``Alert``s at info / warning / critical. A **critical** alert auto-opens a ``Case``
with a remediation SLA so nothing severe sits unactioned. Pure functions over injected repositories;
the service layer records audit entries and publishes the outbound events.
"""
from datetime import datetime, timedelta
from uuid import uuid4

from app.domain.models import Alert, Case, Domain, Severity
from app.repositories import AlertRepository, CaseRepository

# Noise control (FR-019): last time an identical (rule, entity, severity) alert fired.
_recent: dict[tuple[str, str, str], datetime] = {}


def reset_suppression() -> None:
    _recent.clear()


def raise_alert(
    alert_repo: AlertRepository, case_repo: CaseRepository, *,
    rule_id: str, domain: Domain, severity: Severity, entity_id: str = "", message: str = "",
    evidence: dict | None = None, audit_seq: int | None = None, now: datetime, sla_hours: int = 72,
    dedup_window_min: int = 0, force_case: bool = False,
) -> tuple[Alert | None, Case | None, bool]:
    """Raise an alert. Returns (alert, case, suppressed). A repeat non-critical alert inside the
    dedup window is suppressed (alert=None); **criticals are never suppressed** (BR / edge-case).
    A critical (or ``force_case``) alert auto-opens a case."""
    key = (rule_id, entity_id, severity)
    if severity != "critical" and dedup_window_min > 0:
        last = _recent.get(key)
        if last is not None and now - last < timedelta(minutes=dedup_window_min):
            return None, None, True
    _recent[key] = now
    alert = Alert(alert_id=uuid4().hex, rule_id=rule_id, domain=domain, severity=severity,
                  entity_id=entity_id, message=message, evidence=evidence or {},
                  audit_seq=audit_seq, created_at=now)
    alert_repo.add(alert)
    case: Case | None = None
    if severity == "critical" or force_case:         # auto-escalate
        case = Case(case_id=uuid4().hex, rule_id=rule_id, domain=domain, severity=severity,
                    entity_id=entity_id, status="open", evidence=alert.evidence, opened_at=now,
                    sla_due_at=now + timedelta(hours=sla_hours))
        case_repo.add(case)
    return alert, case, False


def update_case(
    case_repo: CaseRepository, *, case_id: str,
    status: str | None = None, assignee: str | None = None,
) -> Case:
    case = case_repo.get(case_id)
    if case is None:
        raise ValueError("unknown_case")
    if status is not None:
        case.status = status
    if assignee is not None:
        case.assignee = assignee
    case_repo.add(case)              # persist the mutation (SQL get() returns a fresh copy)
    return case
