"""Service layer — the cadence orchestration pipeline.

enroll → schedule first step → on each due tick: **guardrail (fail-closed)** → personalize → send
channel provider (retry) → ledger → advance (branch on engagement / linear / complete). Terminal
signals preempt scheduled steps: reply → **pause**, apply/convert → **stop(converted)**, opt-out →
**stop + permanent suppression**. Global frequency cap + TCPA quiet hours + consent are enforced on
every send. Emits ``sequence.enrolled`` / ``outreach.sent`` / ``sequence.stopped`` / … (FR/§6).
"""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app import abtest, branching, events, guardrails, personalize, providers, scheduler
from app.clients import compliance_client
from app.constants import CONVERTED_STAGES
from app.core.config import Settings, get_settings
from app.domain.models import (
    Contact,
    EngagementEvent,
    Enrollment,
    OutreachRecord,
    SequenceDef,
    Step,
)
from app.duration import parse_duration
from app.frequency import get_store as get_freq
from app.repositories import (
    get_audit_repository,
    get_contact_repository,
    get_engagement_repository,
    get_enrollment_repository,
    get_ledger_repository,
    get_sequence_repository,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _audit(action: str, payload: dict) -> None:
    get_audit_repository().add({"action": action, "payload": payload, "at": _now().isoformat()})


# ── Definitions / contacts ─────────────────────────────────────────────────
def upsert_sequence(seq: SequenceDef) -> SequenceDef:
    get_sequence_repository().put(seq)
    _audit("sequence_upserted", {"sequence_id": seq.sequence_id, "version": seq.version})
    return seq


def upsert_contact(contact: Contact) -> Contact:
    get_contact_repository().put(contact)
    return contact


def _step(seq: SequenceDef, step_id: str) -> Step | None:
    return next((s for s in seq.steps if s.id == step_id), None)


def _next_linear(seq: SequenceDef, step_id: str) -> str | None:
    ids = [s.id for s in seq.steps]
    i = ids.index(step_id) if step_id in ids else -1
    return ids[i + 1] if 0 <= i < len(ids) - 1 else None


def _max_steps(seq: SequenceDef) -> int | None:
    for rule in seq.stop_rules:
        if rule.startswith("max_steps:"):
            try:
                return int(rule.split(":", 1)[1])
            except ValueError:
                return None
    return None


def _occurrence(enrollment: Enrollment, step: Step, now: datetime) -> int:
    """Return the next occurrence number for this (enrollment, step)."""
    return (
        sum(
            1
            for r in get_ledger_repository().for_enrollment(enrollment.enrollment_id)
            if r.step_id == step.id and r.status in ("sent", "failed", "blocked")
        )
        + 1
    )


def _already_attempted(enrollment: Enrollment, step: Step, now: datetime) -> bool:
    """Idempotency guard: a final attempt exists within the de-dupe window."""
    window_start = now - _IDEMPOTENCY_WINDOW
    for r in get_ledger_repository().for_enrollment(enrollment.enrollment_id):
        if r.step_id == step.id and r.status in ("sent", "failed", "blocked") \
                and r.sent_at >= window_start:
            return True
    return False


_IDEMPOTENCY_WINDOW = timedelta(seconds=300)


# ── Enrollment ─────────────────────────────────────────────────────────────
def enroll(contact_id: str, sequence_id: str, *, trigger: str = "manual",
           context: dict | None = None, version: int | None = None, tenant: str = "default",
           now: datetime | None = None) -> dict:
    now = now or _now()
    settings = get_settings()
    contact = get_contact_repository().get(contact_id, tenant)
    if contact is None:
        return {"status": "error", "reason": "unknown_contact"}
    if contact.opted_out or contact.dnc:                      # never enroll a suppressed contact
        return {"status": "blocked", "reason": "opted_out" if contact.opted_out else "dnc"}
    seq = get_sequence_repository().get(sequence_id, tenant, version)
    if seq is None or not seq.steps:
        return {"status": "error", "reason": "unknown_sequence"}

    repo = get_enrollment_repository()
    existing = next((e for e in repo.active_for_contact(contact_id, tenant)
                     if e.sequence_id == sequence_id), None)
    if existing is not None:                                  # idempotent: one active/(contact,seq)
        return {"status": "duplicate", "enrollment": existing.model_dump(mode="json")}

    step0 = seq.steps[0]
    enrollment = Enrollment(
        enrollment_id=uuid4().hex, contact_id=contact_id, sequence_id=sequence_id, tenant_id=tenant,
        version=seq.version, state="active", phase="send", current_step_id=step0.id,
        next_action_at=scheduler.next_send_at(now, parse_duration(step0.delay), contact, settings),
        trigger=trigger, context=context or {}, enrolled_at=now)
    repo.put(enrollment)
    events.publish(events.EVENT_ENROLLED, enrollment.model_dump(mode="json"))
    _audit("enrolled", {"enrollment_id": enrollment.enrollment_id, "trigger": trigger})
    return {"status": "enrolled", "enrollment": enrollment.model_dump(mode="json")}


# ── Scheduler tick — advance all due enrollments ───────────────────────────
def tick(*, now: datetime | None = None, tenant: str | None = None) -> dict:
    now = now or _now()
    settings = get_settings()
    processed = sent = held = stopped = 0
    for enrollment in get_enrollment_repository().due(now, tenant):
        contact = get_contact_repository().get(enrollment.contact_id, enrollment.tenant_id)
        seq = get_sequence_repository().get(enrollment.sequence_id, enrollment.tenant_id,
                                            enrollment.version)
        if contact is None or seq is None:
            continue
        outcome = _process(enrollment, contact, seq, now=now, settings=settings)
        processed += 1
        sent += outcome == "sent"
        held += outcome == "held"
        stopped += outcome in ("stopped", "completed")
    return {"processed": processed, "sent": sent, "held": held, "closed": stopped}


def _process(enrollment: Enrollment, contact: Contact, seq: SequenceDef, *, now: datetime,
             settings: Settings) -> str:
    step = _step(seq, enrollment.current_step_id)
    if step is None:
        _finish(enrollment, "completed", now)
        return "completed"
    if enrollment.phase == "send":
        return _process_send(enrollment, contact, seq, step, now=now, settings=settings)
    return _process_branch(enrollment, contact, seq, step, now=now, settings=settings)


def _process_send(enrollment: Enrollment, contact: Contact, seq: SequenceDef, step: Step, *,
                  now: datetime, settings: Settings) -> str:
    if _already_attempted(enrollment, step, now):
        _ledger(enrollment, step, "held", _occurrence(enrollment, step, now), contact,
                block_reason="duplicate_attempt", now=now)
        return "held"

    decision = guardrails.check(
        contact,
        sequence_id=seq.sequence_id,
        now=now,
        settings=settings,
        freq=get_freq(),
        per_seq_day=seq.freq_cap_day,
        per_seq_week=seq.freq_cap_week,
    )
    occ = _occurrence(enrollment, step, now)

    if decision.disposition == "stop":                       # opt-out / DNC → terminal
        _ledger(enrollment, step, "blocked", occ, contact, block_reason=decision.reason, now=now)
        _finish(enrollment, "opt_out" if decision.reason == "opted_out" else decision.reason, now)
        return "stopped"
    if decision.disposition == "hold":                       # quiet-hours / freq-cap / consent
        _ledger(enrollment, step, "held", occ, contact, block_reason=decision.reason, now=now)
        enrollment.next_action_at = decision.retry_at or now
        get_enrollment_repository().put(enrollment)
        return "held"

    variant, template = abtest.allocate(step, enrollment.enrollment_id)   # A/B
    body = personalize.render(template, contact, enrollment.context)      # {{vars}}

    # Centralised compliance precheck (P4): consult the Compliance Monitor before sending.
    cms = compliance_client.precheck(
        contact, step.channel, body, now, settings=settings
    )
    if cms is not None and cms.get("decision") == "deny":
        reasons = cms.get("reasons", [])
        block_reason = "compliance:" + ",".join(reasons) if reasons else "compliance:deny"
        _ledger(enrollment, step, "blocked", occ, contact, template=template, rendered=body,
                block_reason=block_reason, variant=variant, now=now)
        if any(r in ("opt_out", "dnd_registry", "no_consent") for r in reasons):
            _finish(enrollment, "opt_out" if "opt_out" in reasons else "compliance", now)
            return "stopped"
        return "held"

    res = providers.deliver(contact, step.channel, body, max_attempts=settings.provider_retry_max)
    status = "sent" if res.success else "failed"
    _ledger(enrollment, step, status, occ, contact, template=template, rendered=body,
            provider=res.provider, attempts=res.attempts, variant=variant, now=now)
    if res.success:
        get_freq().increment(contact.tenant_id, contact.contact_id, seq.sequence_id, now)
        enrollment.steps_sent += 1
        enrollment.variant = variant
        events.publish(events.EVENT_SENT, {"enrollment_id": enrollment.enrollment_id,
                       "contact_id": contact.contact_id, "step_id": step.id,
                       "channel": step.channel, "consent_basis": contact.consent_basis})
    else:
        events.publish(events.EVENT_FAILED, {"enrollment_id": enrollment.enrollment_id,
                       "step_id": step.id, "channel": step.channel, "attempts": res.attempts})
        events.dead_letter({"enrollment_id": enrollment.enrollment_id, "step_id": step.id},
                           reason=f"send_failed:{res.error}")

    max_steps = _max_steps(seq)
    if max_steps is not None and enrollment.steps_sent >= max_steps:
        _finish(enrollment, "max_steps", now)
        return "completed"
    enrollment.phase = "branch"                    # evaluate branches on the next tick
    enrollment.next_action_at = now
    get_enrollment_repository().put(enrollment)
    return "sent" if res.success else "held"


def _process_branch(enrollment: Enrollment, contact: Contact, seq: SequenceDef, step: Step, *,
                    now: datetime, settings: Settings) -> str:
    engaged = get_engagement_repository().kinds_for_step(enrollment.enrollment_id, step.id)
    sent_rows = [r for r in get_ledger_repository().for_enrollment(enrollment.enrollment_id)
                 if r.step_id == step.id and r.status == "sent"]
    elapsed = (now - sent_rows[-1].sent_at) if sent_rows else branching.branch_wait(step)
    next_id = branching.next_step_from_branches(step, engaged=engaged, elapsed=elapsed)
    if next_id is None:
        # No branch yet — if a timed-negative could still fire, wait; else fall through.
        wait = branching.remaining_wait(step, engaged=engaged, elapsed=elapsed)  # timed-negatives
        if wait is not None:
            enrollment.next_action_at = now + wait
            get_enrollment_repository().put(enrollment)
            return "waiting"
        next_id = _next_linear(seq, step.id)
    if next_id is None:
        _finish(enrollment, "completed", now)
        return "completed"
    next_step = _step(seq, next_id)
    enrollment.current_step_id = next_id
    enrollment.phase = "send"
    enrollment.next_action_at = scheduler.next_send_at(
        now, parse_duration(next_step.delay if next_step else "PT0M"), contact, settings)
    get_enrollment_repository().put(enrollment)
    return "branched"


def _ledger(enrollment: Enrollment, step: Step, status: str, occurrence: int, contact: Contact, *,
            template: str = "", rendered: str = "", provider: str = "", attempts: int = 1,
            block_reason: str = "", variant: str = "", now: datetime | None = None) -> None:
    sent_at = now if now is not None else _now()
    get_ledger_repository().add(OutreachRecord(
        id=uuid4().hex, enrollment_id=enrollment.enrollment_id, contact_id=enrollment.contact_id,
        tenant_id=enrollment.tenant_id, step_id=step.id, channel=step.channel,
        consent_basis=contact.consent_basis, status=status, occurrence=occurrence,   # type: ignore[arg-type]
        template=template, rendered=rendered, provider=provider, attempts=attempts,
        block_reason=block_reason, sent_at=sent_at))


def _finish(enrollment: Enrollment, reason: str, now: datetime, *, state: str = "stopped") -> None:
    enrollment.state = "completed" if reason in ("completed", "max_steps") else state  # type: ignore[assignment]
    enrollment.stopped_reason = reason
    enrollment.stopped_at = now
    get_enrollment_repository().put(enrollment)
    topic = events.EVENT_COMPLETED if enrollment.state == "completed" else events.EVENT_STOPPED
    events.publish(topic, {"enrollment_id": enrollment.enrollment_id,
                   "contact_id": enrollment.contact_id, "reason": reason})
    _audit("sequence_" + ("completed" if enrollment.state == "completed" else "stopped"),
           {"enrollment_id": enrollment.enrollment_id, "reason": reason})


# ── Terminal signals — preempt scheduled steps ─────────────────────────────
def _active(contact_id: str, tenant: str) -> list[Enrollment]:
    return [e for e in get_enrollment_repository().active_for_contact(contact_id, tenant)
            if e.state == "active"]


def ingest_engagement(event: EngagementEvent) -> dict:
    get_engagement_repository().add(event)                    # feeds branching + analytics
    if event.kind == "reply":                                 # FR: reply → pause the sequence
        for e in _active(event.contact_id, event.tenant_id):
            _finish(e, "replied", _now(), state="paused")
    return {"status": "recorded", "kind": event.kind}


def ingest_raw(payload: dict) -> dict:
    try:
        event = EngagementEvent(**payload)
    except Exception as exc:  # noqa: BLE001 — malformed event → dead-letter
        events.dead_letter(payload, reason=str(exc))
        return {"status": "dead_lettered"}
    return ingest_engagement(event)


def record_stage_change(contact_id: str, stage: str, *, tenant: str = "default") -> dict:
    contact = get_contact_repository().get(contact_id, tenant)
    if contact is not None:
        contact.deal_stage = stage
        get_contact_repository().put(contact)
    stopped = 0
    if stage.lower() in CONVERTED_STAGES:                     # applied/converted → exit sequence
        for e in _active(contact_id, tenant):
            _finish(e, "converted", _now())
            stopped += 1
    return {"contact_id": contact_id, "stage": stage, "stopped": stopped}


def opt_out(contact_id: str, *, tenant: str = "default") -> dict:
    contact = get_contact_repository().get(contact_id, tenant)
    if contact is not None:
        contact.opted_out = True                             # permanent suppression
        get_contact_repository().put(contact)
    stopped = 0
    for e in _active(contact_id, tenant):
        _finish(e, "opt_out", _now())
        stopped += 1
    _audit("opt_out", {"contact_id": contact_id, "stopped": stopped})
    return {"contact_id": contact_id, "opted_out": True, "stopped": stopped}


def update_consent(contact_id: str, *, tenant: str = "default", consent_basis: str | None = None,
                   dnc: bool | None = None, opted_out: bool | None = None) -> dict:
    contact = get_contact_repository().get(contact_id, tenant)
    if contact is None:
        return {"status": "error", "reason": "unknown_contact"}
    if consent_basis is not None:
        contact.consent_basis = consent_basis
    if dnc is not None:
        contact.dnc = dnc
    if opted_out is not None:
        contact.opted_out = opted_out
    get_contact_repository().put(contact)
    if contact.opted_out or contact.dnc:                     # propagate suppression to cadences
        for e in _active(contact_id, tenant):
            _finish(e, "opt_out" if contact.opted_out else "dnc", _now())
    return {"contact_id": contact_id, "consent_basis": contact.consent_basis,
            "dnc": contact.dnc, "opted_out": contact.opted_out}


def mark_manual_outreach(contact_id: str, *, tenant: str = "default") -> dict:
    contact = get_contact_repository().get(contact_id, tenant)
    if contact is None:
        return {"status": "error", "reason": "unknown_contact"}
    contact.last_manual_outreach_at = _now()                 # pause automation for the cooldown
    get_contact_repository().put(contact)
    return {"contact_id": contact_id, "cooldown_started": True}


def set_enrollment_state(enrollment_id: str, *, state: str) -> dict:
    e = get_enrollment_repository().get(enrollment_id)
    if e is None:
        return {"status": "error", "reason": "unknown_enrollment"}
    if state not in ("paused", "resume", "stopped"):
        return {"status": "error", "reason": "invalid_state"}
    if state == "resume" and e.state == "paused":
        e.state = "active"
        e.next_action_at = _now()
    elif state in ("paused", "stopped"):
        e.state = state  # type: ignore[assignment]
        e.stopped_reason = e.stopped_reason or "manual"
        e.stopped_at = _now()
    get_enrollment_repository().put(e)
    return {"enrollment_id": enrollment_id, "state": e.state}


# ── Auto-enroll triggers ───────────────────────────────────────────────────
def handle_lead_scored(contact_id: str, score: float, sequence_id: str, *, tenant: str = "default",
                       context: dict | None = None) -> dict:
    if score < get_settings().lead_score_threshold:
        return {"status": "skipped", "reason": "below_threshold", "score": score}
    return enroll(contact_id, sequence_id, trigger="lead_score", context=context, tenant=tenant)


def handle_tag_applied(contact_id: str, tag: str, sequence_id: str, *,
                       tenant: str = "default") -> dict:
    return enroll(contact_id, sequence_id, trigger="tag_applied",
                  context={"tag": tag}, tenant=tenant)


# ── Queries ────────────────────────────────────────────────────────────────
def get_enrollment(enrollment_id: str) -> dict | None:
    e = get_enrollment_repository().get(enrollment_id)
    if e is None:
        return None
    history = [{"step_id": r.step_id, "channel": r.channel, "status": r.status,
                "consent_basis": r.consent_basis, "block_reason": r.block_reason,
                "sent_at": r.sent_at.isoformat()}
               for r in get_ledger_repository().for_enrollment(enrollment_id)]
    return {**e.model_dump(mode="json"), "history": history}


def list_enrollments(*, contact_id: str | None = None, tenant: str = "default") -> list[dict]:
    repo = get_enrollment_repository()
    items = repo.for_contact(contact_id, tenant) if contact_id else repo.list(tenant)
    return [e.model_dump(mode="json") for e in items]


def outreach_ledger(*, tenant: str = "default", limit: int = 200) -> list[dict]:
    return [r.model_dump(mode="json") for r in get_ledger_repository().list(tenant)[-limit:]]
