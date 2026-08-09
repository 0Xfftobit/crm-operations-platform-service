"""Repositories behind a narrow interface (in-memory default; Postgres via STORAGE_GATEWAY next).

The append-only audit ledger lives in ``app.audit``; these hold the mutable operational state —
consent, alerts, cases, disclosure timers, explainability refs, rules/versions, and tracked regs.
"""
from __future__ import annotations  # a `list` method must not shadow list[...] annotations

from functools import wraps
from threading import RLock

from app.core.config import get_settings
from app.domain.models import (
    Alert,
    Case,
    CompliancePolicy,
    ComplianceRule,
    Consent,
    DisclosureTimer,
    Regulation,
    RetentionRecord,
    RuleChange,
)


def _synchronized(fn):
    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    return wrapper


class ConsentRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._items: dict[tuple[str, str], Consent] = {}

    @_synchronized
    def put(self, c: Consent) -> None:
        self._items[(c.lead_id, c.channel)] = c

    @_synchronized
    def get(self, lead_id: str, channel: str) -> Consent | None:
        return self._items.get((lead_id, channel))

    @_synchronized
    def list(self) -> list[Consent]:
        return list(self._items.values())

    @_synchronized
    def clear(self) -> None:
        self._items.clear()


class AlertRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._items: list[Alert] = []

    @_synchronized
    def add(self, a: Alert) -> None:
        self._items.append(a)

    @_synchronized
    def list(self) -> list[Alert]:
        return list(self._items)

    @_synchronized
    def clear(self) -> None:
        self._items.clear()


class CaseRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._items: dict[str, Case] = {}

    @_synchronized
    def add(self, c: Case) -> None:
        self._items[c.case_id] = c

    @_synchronized
    def get(self, case_id: str) -> Case | None:
        return self._items.get(case_id)

    @_synchronized
    def list(self) -> list[Case]:
        return list(self._items.values())

    @_synchronized
    def clear(self) -> None:
        self._items.clear()


class DisclosureRepository:
    """Keyed by (deal_id, kind) so a deal can track multiple disclosures at different stages."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._items: dict[tuple[str, str], DisclosureTimer] = {}

    @_synchronized
    def put(self, t: DisclosureTimer) -> None:
        self._items[(t.deal_id, t.kind)] = t

    @_synchronized
    def get(self, deal_id: str, kind: str = "adverse_action") -> DisclosureTimer | None:
        return self._items.get((deal_id, kind))

    @_synchronized
    def list(self) -> list[DisclosureTimer]:
        return list(self._items.values())

    @_synchronized
    def clear(self) -> None:
        self._items.clear()


class ExplainabilityRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._items: dict[str, dict] = {}

    @_synchronized
    def put(self, ref: str, record: dict) -> None:
        self._items[ref] = record

    @_synchronized
    def get(self, ref: str) -> dict | None:
        return self._items.get(ref)

    @_synchronized
    def clear(self) -> None:
        self._items.clear()


class RuleRepository:
    """Rules keyed by (rule_id, stage) so staging + production coexist."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._items: dict[tuple[str, str], ComplianceRule] = {}

    @_synchronized
    def put(self, r: ComplianceRule) -> None:
        self._items[(r.rule_id, r.stage)] = r

    @_synchronized
    def get(self, rule_id: str, stage: str = "production") -> ComplianceRule | None:
        return self._items.get((rule_id, stage))

    @_synchronized
    def list(self) -> list[ComplianceRule]:
        return list(self._items.values())

    @_synchronized
    def clear(self) -> None:
        self._items.clear()


class RuleChangeRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._items: dict[str, RuleChange] = {}

    @_synchronized
    def add(self, c: RuleChange) -> None:
        self._items[c.change_id] = c

    @_synchronized
    def get(self, change_id: str) -> RuleChange | None:
        return self._items.get(change_id)

    @_synchronized
    def for_rule(self, rule_id: str) -> list[RuleChange]:
        return sorted((c for c in self._items.values() if c.rule_id == rule_id),
                      key=lambda c: c.new_version)

    @_synchronized
    def list(self) -> list[RuleChange]:
        return list(self._items.values())

    @_synchronized
    def clear(self) -> None:
        self._items.clear()


class RegulationRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._items: dict[str, Regulation] = {}

    @_synchronized
    def put(self, r: Regulation) -> None:
        self._items[r.reg_id] = r

    @_synchronized
    def get(self, reg_id: str) -> Regulation | None:
        return self._items.get(reg_id)

    @_synchronized
    def list(self) -> list[Regulation]:
        return list(self._items.values())

    @_synchronized
    def clear(self) -> None:
        self._items.clear()


class PolicyRepository:
    """Single-slot: holds the highest-version Engine-8 policy (version reconciliation)."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._active: CompliancePolicy | None = None

    @_synchronized
    def put(self, p: CompliancePolicy) -> bool:
        """Store if newer; return True when it becomes active."""
        if self._active is None or p.version >= self._active.version:
            self._active = p
            return True
        return False

    @_synchronized
    def active(self) -> CompliancePolicy | None:
        return self._active

    @_synchronized
    def clear(self) -> None:
        self._active = None


class RetentionRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._items: dict[str, RetentionRecord] = {}

    @_synchronized
    def put(self, r: RetentionRecord) -> None:
        self._items[r.lead_id] = r

    @_synchronized
    def get(self, lead_id: str) -> RetentionRecord | None:
        return self._items.get(lead_id)

    @_synchronized
    def list(self) -> list[RetentionRecord]:
        return list(self._items.values())

    @_synchronized
    def clear(self) -> None:
        self._items.clear()


_consent = ConsentRepository()
_alerts = AlertRepository()
_cases = CaseRepository()
_disclosures = DisclosureRepository()
_explain = ExplainabilityRepository()
_rules = RuleRepository()
_rule_changes = RuleChangeRepository()
_regulations = RegulationRepository()
_policy = PolicyRepository()
_retention = RetentionRepository()


def _sql():
    from app.db import sql_repositories as sr
    return sr


def get_consent_repository():
    return _sql().SqlConsentRepository() if get_settings().uses_sql else _consent


def get_alert_repository():
    return _sql().SqlAlertRepository() if get_settings().uses_sql else _alerts


def get_case_repository():
    return _sql().SqlCaseRepository() if get_settings().uses_sql else _cases


def get_disclosure_repository():
    return _sql().SqlDisclosureRepository() if get_settings().uses_sql else _disclosures


def get_explainability_repository():
    return _sql().SqlExplainabilityRepository() if get_settings().uses_sql else _explain


def get_rule_repository():
    return _sql().SqlRuleRepository() if get_settings().uses_sql else _rules


def get_rule_change_repository():
    return _sql().SqlRuleChangeRepository() if get_settings().uses_sql else _rule_changes


def get_regulation_repository():
    return _sql().SqlRegulationRepository() if get_settings().uses_sql else _regulations


def get_policy_repository():
    return _sql().SqlPolicyRepository() if get_settings().uses_sql else _policy


def get_retention_repository():
    return _sql().SqlRetentionRepository() if get_settings().uses_sql else _retention


def reset_repositories() -> None:
    """Clear the in-memory singletons (SQL test isolation is handled by a per-test database)."""
    for repo in (_consent, _alerts, _cases, _disclosures, _explain, _rules, _rule_changes,
                 _regulations, _policy, _retention):
        repo.clear()
