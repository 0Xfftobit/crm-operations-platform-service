"""Domain models for the Follow-up Sequences Engine (FSE).

A ``SequenceDef`` is a versioned cadence of ``Step``s (channel + delay + template + branches). A
``Contact`` carries the consent/DNC/timezone/personalization data the guardrails and personalizer
need. An ``Enrollment`` is a per-contact durable state machine (active/paused/stopped/completed)
the scheduler advances step by step; every send is written to the append-only ``OutreachRecord``
ledger with its consent basis. Engagement webhooks land as ``EngagementEvent``s that drive branching
and stop conditions.
"""
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Channel = Literal["sms", "email", "whatsapp", "voicemail", "call_task"]
EnrollmentState = Literal["active", "paused", "stopped", "completed"]
Phase = Literal["send", "branch"]
EngagementKind = Literal["delivered", "open", "click", "reply", "bounce"]


def _as_utc(v: datetime) -> datetime:
    return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


class Branch(BaseModel):
    """A next-step rule: if ``cond`` holds, go to ``goto``. Conditions: opened|clicked|replied|
    no_open_after:PT48H|no_reply_after:PT48H|no_click_after:PT24H."""

    cond: str = Field(alias="if")
    goto: str

    model_config = {"populate_by_name": True}


class Step(BaseModel):
    id: str
    channel: Channel = "email"
    delay: str = "PT0M"                       # ISO-8601 wait before sending this step
    template: str = ""                        # template id/body with {{variables}}
    branches: list[Branch] = Field(default_factory=list)
    variants: dict[str, str] = Field(default_factory=dict)   # A/B: variant_name -> template


class SequenceDef(BaseModel):
    sequence_id: str
    tenant_id: str = "default"
    name: str
    version: int = 1
    steps: list[Step] = Field(default_factory=list)
    stop_rules: list[str] = Field(default_factory=list)      # replied|converted|opt_out|max_steps:7
    # per-sequence daily/weekly cap (falls back to global if None)
    freq_cap_day: int | None = None
    freq_cap_week: int | None = None
    status: Literal["draft", "active", "archived"] = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Contact(BaseModel):
    contact_id: str
    tenant_id: str = "default"
    name: str = ""                            # {{lead_name}}
    property_name: str = ""                   # {{property_name}}
    agent_name: str = ""                      # {{agent_name}}
    timezone: str = ""                        # IANA tz for quiet-hours (falls back to default)
    email: str = ""
    phone: str = ""
    whatsapp: str = ""
    consent_basis: str = "unknown"            # express_written|express|prior_business|unknown
    dnc: bool = False                         # do-not-call
    opted_out: bool = False                   # unsubscribed (permanent suppression)
    channel_prefs: list[str] = Field(default_factory=list)
    deal_stage: str = ""
    lead_score: float = 0.0
    tags: list[str] = Field(default_factory=list)
    last_manual_outreach_at: datetime | None = None
    best_time_hour: int | None = None         # from Timing Optimizing Engine (local hour)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("last_manual_outreach_at")
    @classmethod
    def _tz(cls, v: datetime | None) -> datetime | None:
        return _as_utc(v) if v else v


class Enrollment(BaseModel):
    enrollment_id: str
    contact_id: str
    sequence_id: str
    tenant_id: str = "default"
    version: int = 1
    state: EnrollmentState = "active"
    phase: Phase = "send"                     # send this step, or evaluate its branches next
    current_step_id: str = ""
    next_action_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    variant: str = ""
    steps_sent: int = 0                       # for max_steps stop rule
    trigger: str = "manual"
    context: dict = Field(default_factory=dict)   # deal_id, grade, …
    enrolled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    stopped_reason: str = ""
    stopped_at: datetime | None = None


class OutreachRecord(BaseModel):
    """Append-only outreach ledger row (§2/§8) — links each attempted send to its consent basis."""

    id: str
    enrollment_id: str
    contact_id: str
    tenant_id: str = "default"
    step_id: str
    channel: Channel
    consent_basis: str = "unknown"
    status: Literal["sent", "failed", "held", "blocked"]
    occurrence: int = 1                       # idempotency: (enrollment, step, occurrence)
    template: str = ""
    rendered: str = ""
    provider: str = ""
    block_reason: str = ""
    attempts: int = 1
    sent_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EngagementEvent(BaseModel):
    enrollment_id: str = ""
    contact_id: str
    tenant_id: str = "default"
    step_id: str = ""
    kind: EngagementKind
    channel: Channel = "email"
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("at")
    @classmethod
    def _tz(cls, v: datetime) -> datetime:
        return _as_utc(v)
