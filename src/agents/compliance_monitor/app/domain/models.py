"""Domain models for the Compliance Monitor System.

The backbone is an **append-only, hash-chained AuditEntry** ledger. Activities flow in as
``ActivityEvent``s screened by detectors that emit ``Alert``s (critical ones open a ``Case``);
outbound comms pass a ``PrecheckRequest`` → ``PrecheckResult`` (fail-closed). ``Consent`` governs
comms, ``DisparityResult`` monitors fair-lending, ``DisclosureTimer`` tracks ECOA clocks, and the
``ComplianceRule`` / ``Regulation`` set is version-controlled through a two-step approval workflow.
"""
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["info", "warning", "critical"]
Channel = Literal["sms", "email", "call", "whatsapp", "push"]
Decision = Literal["allow", "deny"]
Domain = Literal["data_privacy", "marketing", "ai_fairness", "regulatory", "audit"]
RAG = Literal["green", "amber", "red"]


class ActivityEvent(BaseModel):
    """A monitored activity from a peer engine (comm, decision, record change, override…)."""

    model_config = ConfigDict(extra="ignore")

    event_id: str = ""
    event_type: str                        # comm_sent | decision | record_change | consent_update…
    actor_type: str = "engine"             # engine | user | system
    actor_id: str = ""
    engine: str = ""                       # source engine
    entity_type: str = ""                  # lead | deal | agent…
    entity_id: str = ""
    action: str = ""
    reasoning: str = ""
    outcome: str = ""
    explain_ref: str = ""                  # pointer to reason codes / features (AI decisions)
    ip_address: str = ""
    session_id: str = ""
    source_seq: int | None = None          # per-source monotonic seq → coverage-gap detection
    payload: dict = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuditEntry(BaseModel):
    """One immutable, hash-chained ledger record (design §2)."""

    seq: int
    event_id: str
    occurred_at: datetime
    actor_type: str = "system"
    actor_id: str = ""
    event_type: str
    entity_type: str = ""
    entity_id: str = ""
    action: str = ""
    reasoning: str = ""
    outcome: str = ""
    engine: str = ""
    explain_ref: str = ""
    ip_address: str = ""
    session_id: str = ""
    prev_hash: str
    entry_hash: str


class Consent(BaseModel):
    lead_id: str
    channel: Channel
    opted_in: bool = True
    on_dnd_registry: bool = False          # TRAI DND / DNC
    timezone: str = "UTC"
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PrecheckRequest(BaseModel):
    channel: Channel
    contact_id: str
    content: str = ""
    sender_id: str = ""                    # CAN-SPAM sender identity (email)
    has_unsubscribe: bool = False          # CAN-SPAM unsubscribe affordance (email)
    scheduled_for: datetime | None = None  # for quiet-hours evaluation (contact local tz)


class PrecheckResult(BaseModel):
    decision: Decision
    reasons: list[str] = Field(default_factory=list)
    audit_seq: int | None = None
    latency_ms: float | None = None        # measured sync hard-rule latency (budget-enforced)


class DisparityResult(BaseModel):
    sub_agent: str
    cohort_dimension: str
    selection_rates: dict[str, float]
    ratio: float                           # min-rate / max-rate (4/5ths basis)
    passes: bool
    min_sample_met: bool
    window: str = ""


class DisclosureTimer(BaseModel):
    deal_id: str
    kind: str = "adverse_action"           # adverse_action | loan_estimate | closing_disclosure…
    stage: str = ""                        # pipeline stage that required this disclosure
    started_at: datetime
    due_at: datetime
    satisfied: bool = False


class Alert(BaseModel):
    alert_id: str
    rule_id: str
    domain: Domain
    severity: Severity
    entity_id: str = ""
    message: str = ""
    evidence: dict = Field(default_factory=dict)
    audit_seq: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Case(BaseModel):
    case_id: str
    rule_id: str
    domain: Domain
    severity: Severity
    entity_id: str = ""
    status: Literal["open", "investigating", "remediated", "closed"] = "open"
    assignee: str = ""
    evidence: dict = Field(default_factory=dict)
    opened_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sla_due_at: datetime | None = None


class ComplianceRule(BaseModel):
    rule_id: str
    domain: Domain
    version: int = 1
    definition: dict = Field(default_factory=dict)
    stage: Literal["staging", "production"] = "production"
    effective_date: datetime | None = None


class RuleChange(BaseModel):
    """Version-controlled change record — proposed by one user, approved by another (two-step)."""

    change_id: str
    rule_id: str
    previous_version: int
    new_version: int
    definition: dict
    change_reason: str = ""
    changed_by: str                        # proposer
    approved_by: str = ""                   # different user
    status: Literal["proposed", "approved", "deployed", "rejected"] = "proposed"
    emergency: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    effective_date: datetime | None = None


class Regulation(BaseModel):
    reg_id: str
    name: str
    authority: str = ""                    # RERA / TRAI / PDPA / GDPR…
    last_reviewed: datetime | None = None
    status: Literal["current", "review_needed", "updated"] = "current"
    source_url: str = ""


class CompliancePolicy(BaseModel):
    """Signed, versioned policy thresholds consumed from Engine 8 (FR-014).

    ``thresholds`` maps known knobs (fairness_ratio_min/max, disparity_min_sample,
    adverse_action_days, quiet_hours_start/end, precheck_latency_budget_ms) → override values.
    The highest version wins (version reconciliation).
    """

    policy_id: str = "engine8-policy"
    version: int
    thresholds: dict = Field(default_factory=dict)
    signed_by: str = ""
    effective_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RetentionRecord(BaseModel):
    """Tracks a lead's data for retention enforcement + right-to-erasure (Data Privacy domain)."""

    lead_id: str
    category: str = "lead_data"
    retained_until: datetime
    anonymized: bool = False
    erased: bool = False
    erasure_reason: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
