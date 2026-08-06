"""Rule versioning + two-step approval workflow (CEAAPSS-565 / US-5 / BR-6).

A change is **proposed** by one user (materialised as a ``staging`` rule), **approved** by a
*different* user (separation of duties — a proposer can never approve their own change), then
**deployed**, which promotes the staged definition to ``production`` with a new version.
``emergency_override`` collapses the three steps for incidents but stamps ``emergency=True``.

Pure functions over the injected repositories; the service layer writes the audit entries.
"""
from datetime import datetime
from uuid import uuid4

from app.domain.models import ComplianceRule, Domain, RuleChange
from app.repositories import RuleChangeRepository, RuleRepository


def _next_version(rule_repo: RuleRepository, rule_id: str) -> tuple[int, int]:
    """Return (previous_version, new_version) from the current production rule (0 if new)."""
    prod = rule_repo.get(rule_id, "production")
    prev = prod.version if prod else 0
    return prev, prev + 1


def propose_change(
    rule_repo: RuleRepository, change_repo: RuleChangeRepository, *,
    rule_id: str, domain: Domain, definition: dict, change_reason: str, changed_by: str,
    emergency: bool = False, now: datetime,
) -> RuleChange:
    prev, new = _next_version(rule_repo, rule_id)
    change = RuleChange(
        change_id=uuid4().hex, rule_id=rule_id, previous_version=prev, new_version=new,
        definition=definition, change_reason=change_reason, changed_by=changed_by,
        status="proposed", emergency=emergency, created_at=now)
    change_repo.add(change)
    # Stage the candidate so it can be reviewed/tested without touching production.
    rule_repo.put(ComplianceRule(rule_id=rule_id, domain=domain, version=new,
                                 definition=definition, stage="staging"))
    return change


def approve_change(
    change_repo: RuleChangeRepository, *, change_id: str, approved_by: str,
) -> RuleChange:
    change = change_repo.get(change_id)
    if change is None:
        raise ValueError("unknown_change")
    if change.status != "proposed":
        raise ValueError(f"not_pending:{change.status}")
    if approved_by == change.changed_by:
        raise ValueError("approver_must_differ_from_proposer")   # separation of duties
    change.status = "approved"
    change.approved_by = approved_by
    change_repo.add(change)          # persist the mutation (SQL get() returns a fresh copy)
    return change


def reject_change(
    change_repo: RuleChangeRepository, *, change_id: str, approved_by: str, reason: str = "",
) -> RuleChange:
    change = change_repo.get(change_id)
    if change is None:
        raise ValueError("unknown_change")
    if change.status != "proposed":
        raise ValueError(f"not_pending:{change.status}")
    change.status = "rejected"
    change.approved_by = approved_by
    change.change_reason = (change.change_reason + f" | rejected: {reason}").strip(" |")
    change_repo.add(change)          # persist the mutation
    return change


def deploy_change(
    rule_repo: RuleRepository, change_repo: RuleChangeRepository, *, change_id: str, now: datetime,
) -> tuple[RuleChange, ComplianceRule]:
    change = change_repo.get(change_id)
    if change is None:
        raise ValueError("unknown_change")
    if change.status != "approved":
        raise ValueError(f"not_approved:{change.status}")
    staged = rule_repo.get(change.rule_id, "staging")
    domain = staged.domain if staged else "regulatory"
    prod = ComplianceRule(rule_id=change.rule_id, domain=domain, version=change.new_version,
                          definition=change.definition, stage="production", effective_date=now)
    rule_repo.put(prod)
    change.status = "deployed"
    change.effective_date = now
    change_repo.add(change)          # persist the mutation
    return change, prod


def emergency_override(
    rule_repo: RuleRepository, change_repo: RuleChangeRepository, *,
    rule_id: str, domain: Domain, definition: dict, change_reason: str, changed_by: str,
    now: datetime,
) -> tuple[RuleChange, ComplianceRule]:
    """Propose→approve→deploy in one step for incidents; flagged ``emergency`` for audit."""
    change = propose_change(rule_repo, change_repo, rule_id=rule_id, domain=domain,
                            definition=definition, change_reason=change_reason,
                            changed_by=changed_by, emergency=True, now=now)
    change.status = "approved"
    change.approved_by = changed_by            # single-actor bypass, but recorded as emergency
    change_repo.add(change)                     # persist before deploy re-reads it (SQL mode)
    # Return deploy's change (status=deployed); our local copy is stale under SQL fresh-read repos.
    deployed, prod = deploy_change(rule_repo, change_repo, change_id=change.change_id, now=now)
    return deployed, prod
