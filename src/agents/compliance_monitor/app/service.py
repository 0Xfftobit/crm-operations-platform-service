"""Service layer — orchestrates ingest → audit → detect → alert/case, and the compliance workflows.

Every state-changing path writes to the immutable ledger (``app.audit``) first, then runs the
relevant detector, raises tiered alerts (critical → auto-case), and publishes outbound signals for
Engine-8 correlation. Repositories hold the mutable operational state; the ledger is the truth.
"""
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import uuid4

from app import (
    audit,
    cache,
    casework,
    coldstore,
    coverage,
    disclosures,
    events,
    fairness,
    merkle,
    pii,
    policies,
    precheck,
    privacy,
    regwatch,
    rules,
)
from app.core.config import Settings, get_settings
from app.domain.models import (
    ActivityEvent,
    Alert,
    Case,
    CompliancePolicy,
    Consent,
    DisclosureTimer,
    DisparityResult,
    Domain,
    PrecheckRequest,
    PrecheckResult,
    Regulation,
    RetentionRecord,
    Severity,
)
from app.repositories import (
    get_alert_repository,
    get_case_repository,
    get_consent_repository,
    get_disclosure_repository,
    get_explainability_repository,
    get_policy_repository,
    get_regulation_repository,
    get_retention_repository,
    get_rule_change_repository,
    get_rule_repository,
)

DOMAINS = ("data_privacy", "marketing", "ai_fairness", "regulatory", "audit")


def _now() -> datetime:
    return datetime.now(UTC)


def _effective() -> Settings:
    """Runtime settings overlaid with the active Engine-8 policy thresholds (FR-014)."""
    return policies.effective_settings(get_settings(), get_policy_repository().active())


def _emit(alert: Alert, case: Case | None, settings: Settings) -> None:
    """Publish an alert (+ case) plus an Engine-8 compliance signal."""
    events.publish(events.EVENT_ALERT, alert.model_dump(mode="json"))
    if case is not None:
        events.publish(events.EVENT_CASE_OPENED, case.model_dump(mode="json"))
    events.publish(events.EVENT_SIGNAL, {
        "engine": settings.engine_id, "domain": alert.domain, "severity": alert.severity,
        "entity_id": alert.entity_id, "rule_id": alert.rule_id, "audit_seq": alert.audit_seq})


def _raise(
    *, rule_id: str, domain: Domain, severity: Severity, entity_id: str = "", message: str = "",
    evidence: dict | None = None, audit_seq: int | None = None, settings: Settings,
    force_case: bool = False,
) -> Alert | None:
    """Raise an alert through the noise-controlled path; returns None when suppressed (FR-019)."""
    alert, case, suppressed = casework.raise_alert(
        get_alert_repository(), get_case_repository(), rule_id=rule_id, domain=domain,
        severity=severity, entity_id=entity_id, message=message, evidence=evidence,
        audit_seq=audit_seq, now=_now(), sla_hours=settings.case_sla_hours,
        dedup_window_min=settings.alert_dedup_window_min, force_case=force_case)
    if suppressed or alert is None:
        return None
    _emit(alert, case, settings)
    return alert


# ── Activity ingest + monitoring detectors ─────────────────────────────────
def record_activity(event: ActivityEvent) -> dict:
    """Append a monitored activity to the ledger and run passive compliance detectors."""
    settings = get_settings()
    # Idempotency: a repeat of an already-audited event_id is deduped, not re-audited (§8).
    if event.event_id and audit.search(event_id=event.event_id, limit=1):
        return {"deduped": True, "event_id": event.event_id, "alerts": []}
    event_id = event.event_id or uuid4().hex
    entry = audit.append(
        event_id=event_id, occurred_at=event.occurred_at, actor_type=event.actor_type,
        actor_id=event.actor_id, event_type=event.event_type, entity_type=event.entity_type,
        entity_id=event.entity_id, action=event.action, reasoning=event.reasoning,
        outcome=event.outcome, engine=event.engine, explain_ref=event.explain_ref,
        ip_address=event.ip_address, session_id=event.session_id)
    # consent.updated events keep the consent store (+ hot cache) in sync system-wide.
    if event.event_type in ("consent.updated", "consent_update") and event.payload:
        try:
            upsert_consent(Consent(**event.payload))
        except Exception:  # noqa: BLE001 — malformed consent payload is logged, not fatal
            pass
    # A pipeline stage change starts every disclosure that stage requires — ECOA adverse-action on
    # DENIED, plus RERA/TILA-RESPA loan-estimate/closing disclosures (US-3 AC1 / FR-006).
    if event.event_type in ("deal.stage_changed", "stage_changed") and event.entity_id:
        on_stage_change(event.entity_id, event.outcome or event.action)
    raised: list[Alert | None] = []
    # Coverage: detect dropped events via per-source sequence gaps (BR-3 — gaps are alerts).
    for missing in coverage.observe(event.engine, event.source_seq, now=_now()) or []:
        raised.append(_raise(
            rule_id="coverage_gap", domain="audit", severity="warning", entity_id=event.engine,
            message=f"Dropped event from {event.engine}: source_seq {missing}",
            evidence={"source": event.engine, "missing_seq": missing},
            audit_seq=entry.seq, settings=settings))
    # AI decisions must carry an explainability reference; a gap opens a case (FR-011 / BR-4 / §8).
    if event.event_type in ("decision", "ai_decision") and event.actor_type == "engine" \
            and not event.explain_ref:
        raised.append(_raise(
            rule_id="explainability_required", domain="ai_fairness", severity="warning",
            entity_id=event.entity_id, message="AI decision missing explainability record",
            evidence={"engine": event.engine, "event_id": event_id},
            audit_seq=entry.seq, settings=settings, force_case=True))
    # Overrides must be justified.
    if event.action == "override" and not event.reasoning:
        raised.append(_raise(
            rule_id="override_requires_reason", domain="regulatory", severity="warning",
            entity_id=event.entity_id, message="Override recorded without justification",
            audit_seq=entry.seq, settings=settings))
    # PII exposure scan over reasoning + payload (FR-007).
    if settings.pii_scan_enabled:
        found = sorted(set(pii.scan(event.reasoning)) | set(pii.scan_payload(event.payload)))
        if found:
            raised.append(_raise(
                rule_id="pii_exposure", domain="data_privacy", severity="warning",
                entity_id=event.entity_id, message=f"PII in activity: {', '.join(found)}",
                evidence={"categories": found, "event_id": event_id},
                audit_seq=entry.seq, settings=settings))
    alerts = [a.model_dump(mode="json") for a in raised if a is not None]
    return {"audit_seq": entry.seq, "event_id": event_id, "alerts": alerts}


# ── Pre-send compliance hook (fail-closed) ─────────────────────────────────
def run_precheck(req: PrecheckRequest) -> PrecheckResult:
    settings = _effective()                      # honour any active Engine-8 policy thresholds
    t0 = perf_counter()
    # Fast path: short-TTL consent cache, falling through to the durable repository on a miss.
    consent = cache.get(req.contact_id, req.channel)
    if consent is None:
        consent = get_consent_repository().get(req.contact_id, req.channel)
        if consent is not None:
            cache.set(consent)
    reasons = precheck.evaluate(req, consent, settings=settings)
    # High-risk PII in the outbound body is itself a hard block (FR-007 + BR-1).
    if settings.pii_scan_enabled:
        pii_found = [c for c in pii.scan(req.content) if c in pii.HIGH_RISK]
        if pii_found:
            reasons = [*reasons, f"pii_exposure:{'+'.join(pii_found)}"]
    # Fail-closed on the latency budget: if the sync hard-rule path couldn't complete in time we
    # deny rather than risk sending un-vetted (§8 'precheck timeout → fail-closed'; NFR p99<100ms).
    latency_ms = (perf_counter() - t0) * 1000
    if latency_ms > settings.precheck_latency_budget_ms:
        reasons = [*reasons, "latency_budget_exceeded"]
    decision = "deny" if reasons else "allow"
    entry = audit.append(
        event_id=uuid4().hex, event_type="comm_precheck", actor_type="system",
        actor_id=settings.engine_id, entity_type="lead", entity_id=req.contact_id,
        action=f"precheck:{req.channel}", reasoning=";".join(reasons), outcome=decision,
        engine=settings.engine_id)
    if decision == "deny":
        _raise(rule_id="precheck_block", domain="marketing", severity="warning",
               entity_id=req.contact_id, message=f"Blocked {req.channel}: {', '.join(reasons)}",
               evidence={"reasons": reasons, "channel": req.channel}, audit_seq=entry.seq,
               settings=settings)
    return PrecheckResult(decision=decision, reasons=reasons, audit_seq=entry.seq,
                          latency_ms=round(latency_ms, 3))


def upsert_consent(consent: Consent) -> Consent:
    settings = get_settings()
    get_consent_repository().put(consent)
    cache.set(consent)                           # write-through so opt-out takes effect at once
    audit.append(event_id=uuid4().hex, event_type="consent_update", actor_type="user",
                 entity_type="lead", entity_id=consent.lead_id,
                 action=f"consent:{consent.channel}", engine=settings.engine_id,
                 outcome="opted_in" if consent.opted_in else "opted_out")
    return consent


# ── Fair-lending disparity (4/5ths) ────────────────────────────────────────
def evaluate_fairness(
    sub_agent: str, cohort_dimension: str, cohorts: dict[str, dict], *, window: str = "",
) -> DisparityResult:
    settings = _effective()
    result = fairness.disparity(sub_agent, cohort_dimension, cohorts, settings=settings,
                                window=window)
    entry = audit.append(
        event_id=uuid4().hex, event_type="fairness_check", actor_type="system",
        actor_id=settings.engine_id, entity_type="sub_agent", entity_id=sub_agent,
        action=f"disparity:{cohort_dimension}", outcome="pass" if result.passes else "fail",
        reasoning=f"ratio={result.ratio}", engine=settings.engine_id)
    # Only alert on a real disparity backed by adequate sample (avoids small-n noise).
    if not result.passes and result.min_sample_met:
        _raise(rule_id="four_fifths_rule", domain="ai_fairness", severity="critical",
               entity_id=sub_agent, message=f"Disparate impact: ratio {result.ratio} < "
               f"{settings.fairness_ratio_min}", evidence=result.model_dump(mode="json"),
               audit_seq=entry.seq, settings=settings)
    return result


# ── Disclosure clocks: ECOA adverse-action + RERA/TILA-RESPA at pipeline stage ─
def start_disclosure(deal_id: str, *, kind: str = "adverse_action", due_days: int | None = None,
                     stage: str = "") -> DisclosureTimer:
    settings = get_settings()
    days = due_days if due_days is not None else settings.adverse_action_days
    timer = disclosures.start(deal_id, kind=kind, due_days=days, now=_now(), stage=stage)
    get_disclosure_repository().put(timer)
    audit.append(event_id=uuid4().hex, event_type="disclosure_started", actor_type="system",
                 entity_type="deal", entity_id=deal_id, action=f"{kind}_clock",
                 outcome=timer.due_at.isoformat(), reasoning=f"stage={stage}",
                 engine=settings.engine_id)
    return timer


def on_stage_change(deal_id: str, stage: str) -> list[dict]:
    """Start every disclosure the reached pipeline stage requires (RERA/TILA-RESPA + ECOA)."""
    started: list[dict] = []
    for kind, due_days in disclosures.requirements_for(stage):
        if get_disclosure_repository().get(deal_id, kind) is None:
            timer = start_disclosure(deal_id, kind=kind, due_days=due_days, stage=stage)
            started.append(timer.model_dump(mode="json"))
    return started


def satisfy_disclosure(deal_id: str, *, kind: str = "adverse_action") -> DisclosureTimer:
    settings = get_settings()
    repo = get_disclosure_repository()
    timer = repo.get(deal_id, kind)
    if timer is None:
        raise ValueError("unknown_disclosure")
    timer.satisfied = True
    repo.put(timer)
    audit.append(event_id=uuid4().hex, event_type="disclosure_satisfied", actor_type="system",
                 entity_type="deal", entity_id=deal_id, action=f"{kind}_sent",
                 outcome="satisfied", engine=settings.engine_id)
    return timer


def check_disclosures() -> list[dict]:
    """Sweep open timers; alert on warnings (T-7d/T-2d) and overdue (critical), per kind."""
    settings = _effective()
    now = _now()
    out: list[dict] = []
    for timer in get_disclosure_repository().list():
        st = disclosures.status(timer, settings=settings, now=now)
        if st["state"] == "overdue":
            _raise(rule_id=f"{timer.kind}_overdue", domain="regulatory", severity="critical",
                   entity_id=timer.deal_id, message=f"{timer.kind} disclosure overdue",
                   evidence=st, audit_seq=None, settings=settings)
        elif st["state"] == "warning":
            _raise(rule_id=f"{timer.kind}_due_soon", domain="regulatory", severity="warning",
                   entity_id=timer.deal_id, message=f"{timer.kind} due in {st['days_left']}d",
                   evidence=st, audit_seq=None, settings=settings)
        out.append({"deal_id": timer.deal_id, "kind": timer.kind, **st})
    return out


# ── Explainability records ─────────────────────────────────────────────────
def record_explainability(ref: str, record: dict) -> str:
    settings = get_settings()
    get_explainability_repository().put(ref, record)
    audit.append(event_id=uuid4().hex, event_type="explainability_recorded", actor_type="engine",
                 entity_type=record.get("entity_type", ""), entity_id=record.get("entity_id", ""),
                 action="explain", explain_ref=ref, engine=record.get("engine", settings.engine_id))
    return ref


# ── Rule-change workflow (two-step approval) ───────────────────────────────
def propose_rule_change(*, rule_id, domain, definition, change_reason, changed_by):
    settings = get_settings()
    change = rules.propose_change(
        get_rule_repository(), get_rule_change_repository(), rule_id=rule_id, domain=domain,
        definition=definition, change_reason=change_reason, changed_by=changed_by, now=_now())
    audit.append(event_id=uuid4().hex, event_type="rule_change", actor_type="user",
                 actor_id=changed_by, entity_type="rule", entity_id=rule_id, action="propose",
                 outcome=change.change_id, reasoning=change_reason, engine=settings.engine_id)
    return change


def approve_rule_change(*, change_id, approved_by):
    settings = get_settings()
    change = rules.approve_change(get_rule_change_repository(), change_id=change_id,
                                  approved_by=approved_by)
    audit.append(event_id=uuid4().hex, event_type="rule_change", actor_type="user",
                 actor_id=approved_by, entity_type="rule", entity_id=change.rule_id,
                 action="approve", outcome=change_id, engine=settings.engine_id)
    return change


def reject_rule_change(*, change_id, approved_by, reason=""):
    settings = get_settings()
    change = rules.reject_change(get_rule_change_repository(), change_id=change_id,
                                 approved_by=approved_by, reason=reason)
    audit.append(event_id=uuid4().hex, event_type="rule_change", actor_type="user",
                 actor_id=approved_by, entity_type="rule", entity_id=change.rule_id,
                 action="reject", outcome=change_id, reasoning=reason, engine=settings.engine_id)
    return change


def deploy_rule_change(*, change_id):
    settings = get_settings()
    change, prod = rules.deploy_change(get_rule_repository(), get_rule_change_repository(),
                                       change_id=change_id, now=_now())
    audit.append(event_id=uuid4().hex, event_type="rule_change", actor_type="system",
                 entity_type="rule", entity_id=change.rule_id, action="deploy",
                 outcome=f"v{prod.version}", engine=settings.engine_id)
    return change, prod


def emergency_rule_override(*, rule_id, domain, definition, change_reason, changed_by):
    settings = get_settings()
    change, prod = rules.emergency_override(
        get_rule_repository(), get_rule_change_repository(), rule_id=rule_id, domain=domain,
        definition=definition, change_reason=change_reason, changed_by=changed_by, now=_now())
    audit.append(event_id=uuid4().hex, event_type="rule_change", actor_type="user",
                 actor_id=changed_by, entity_type="rule", entity_id=rule_id,
                 action="emergency_deploy", outcome=f"v{prod.version}", reasoning=change_reason,
                 engine=settings.engine_id)
    return change, prod


def upsert_regulation(reg: Regulation) -> Regulation:
    get_regulation_repository().put(reg)
    return reg


# ── Engine-8 policy ingestion (FR-014) ─────────────────────────────────────
def ingest_policy(policy: CompliancePolicy) -> dict:
    """Consume a signed, versioned Engine-8 policy; the highest version wins."""
    settings = get_settings()
    activated = get_policy_repository().put(policy)
    audit.append(event_id=uuid4().hex, event_type="policy_update", actor_type="system",
                 actor_id="engine-8", entity_type="policy", entity_id=policy.policy_id,
                 action="ingest", outcome="active" if activated else "stale_ignored",
                 reasoning=f"v{policy.version} by {policy.signed_by}", engine=settings.engine_id)
    return {"policy_id": policy.policy_id, "version": policy.version, "active": activated}


def active_policy() -> CompliancePolicy | None:
    return get_policy_repository().active()


# ── Fair-lending batch / geographic disparity (FR-015) ─────────────────────
def evaluate_fairness_batch(runs: list[dict]) -> dict:
    """Run disparity over several sub-agent/cohort sets; flag geographic (redlining-style) breaches.

    Each run = {sub_agent, cohort_dimension, cohorts, window?}. A failing run whose dimension looks
    geographic (geo/zip/tract/region) is tagged ``redlining_risk``.
    """
    results, redlining = [], []
    for run in runs:
        r = evaluate_fairness(run["sub_agent"], run["cohort_dimension"], run["cohorts"],
                              window=run.get("window", ""))
        dim = run["cohort_dimension"].lower()
        geo = any(tok in dim for tok in ("geo", "zip", "tract", "region", "county", "district"))
        if geo and not r.passes and r.min_sample_met:
            redlining.append(run["sub_agent"])
        results.append(r.model_dump(mode="json"))
    return {"results": results, "redlining_risk": sorted(set(redlining))}


# ── Data-privacy: retention + right-to-erasure (Data Privacy domain) ────────
def _flush_consent_cache(lead_id: str) -> None:
    """Drop cached consent for a lead so an anonymization/opt-out takes effect immediately —
    otherwise the pre-send hook could read a stale opted-in consent within the cache TTL."""
    for c in get_consent_repository().list():
        if c.lead_id == lead_id:
            cache.invalidate(lead_id, c.channel)


def register_retention(lead_id: str, *, category: str = "lead_data") -> RetentionRecord:
    settings = get_settings()
    rec = privacy.register(get_retention_repository(), lead_id=lead_id, category=category,
                           settings=settings, now=_now())
    audit.append(event_id=uuid4().hex, event_type="retention_registered", actor_type="system",
                 entity_type="lead", entity_id=lead_id, action="retention_clock",
                 outcome=rec.retained_until.isoformat(), engine=settings.engine_id)
    return rec


def check_retention() -> list[dict]:
    """Sweep retention clocks: pre-deletion warnings, and auto-anonymize past the deadline."""
    settings = get_settings()
    now = _now()
    out: list[dict] = []
    for rec in list(get_retention_repository().list()):
        st = privacy.retention_state(rec, settings=settings, now=now)
        if st["state"] == "due":
            privacy.anonymize(get_retention_repository(), get_consent_repository(),
                              lead_id=rec.lead_id, now=now, reason="retention_expired")
            _flush_consent_cache(rec.lead_id)
            audit.append(event_id=uuid4().hex, event_type="data_anonymized", actor_type="system",
                         entity_type="lead", entity_id=rec.lead_id, action="retention_enforced",
                         outcome="anonymized", engine=settings.engine_id)
            _raise(rule_id="retention_enforced", domain="data_privacy", severity="info",
                   entity_id=rec.lead_id, message="Lead data anonymized (retention expired)",
                   evidence=st, settings=settings)
            st = {"state": "anonymized", "days_left": st["days_left"]}   # reflect the action taken
        elif st["state"] == "warning":
            _raise(rule_id="retention_pre_deletion", domain="data_privacy", severity="warning",
                   entity_id=rec.lead_id, message=f"Lead data deletion in {st['days_left']}d",
                   evidence=st, settings=settings)
        out.append({"lead_id": rec.lead_id, **st})
    return out


def erase_lead(lead_id: str, *, reason: str = "gdpr_erasure_request") -> RetentionRecord:
    """Right-to-erasure: anonymize the lead + mark removed from training datasets."""
    settings = get_settings()
    rec = privacy.anonymize(get_retention_repository(), get_consent_repository(),
                            lead_id=lead_id, now=_now(), erased=True, reason=reason)
    _flush_consent_cache(lead_id)
    audit.append(event_id=uuid4().hex, event_type="data_erased", actor_type="user",
                 entity_type="lead", entity_id=lead_id, action="right_to_erasure",
                 outcome="erased", reasoning=reason, engine=settings.engine_id)
    return rec


# ── Regulatory-change monitoring (CEAAPSS-565) ─────────────────────────────
def report_regulatory_change(reg_id: str, *, summary: str = "") -> Regulation | None:
    settings = get_settings()
    reg = regwatch.report_change(get_regulation_repository(), reg_id=reg_id, now=_now(),
                                 summary=summary)
    if reg is None:
        return None
    audit.append(event_id=uuid4().hex, event_type="regulation_change", actor_type="system",
                 entity_type="regulation", entity_id=reg_id, action="detected",
                 outcome="review_needed", reasoning=summary, engine=settings.engine_id)
    _raise(rule_id="regulation_changed", domain="regulatory", severity="warning",
           entity_id=reg_id, message=f"Regulation {reg.name} changed — rule review needed",
           evidence={"authority": reg.authority, "summary": summary}, settings=settings)
    return reg


def mark_regulation_reviewed(reg_id: str) -> Regulation | None:
    return regwatch.mark_reviewed(get_regulation_repository(), reg_id=reg_id, now=_now())


def sweep_regulations(*, max_age_days: int = 180) -> list[str]:
    settings = get_settings()
    flagged = regwatch.stale_sweep(get_regulation_repository(), now=_now(),
                                   max_age_days=max_age_days)
    for reg_id in flagged:
        _raise(rule_id="regulation_review_overdue", domain="regulatory", severity="warning",
               entity_id=reg_id, message="Regulation review overdue", settings=settings)
    return flagged


# ── Audit-chain integrity (FR-018) ─────────────────────────────────────────
def integrity_check() -> dict:
    """Verify the hash chain; a break raises a critical audit-domain integrity alert."""
    settings = get_settings()
    result = audit.verify_chain()
    if not result["verified"]:
        _raise(rule_id="audit_chain_break", domain="audit", severity="critical",
               entity_id=str(result["broken_at"]), message="Audit chain integrity broken",
               evidence=result, settings=settings)
    return result


# ── Coverage-gap / dropped-event monitoring (BR-3) ─────────────────────────
def coverage_report() -> dict:
    return coverage.report()


def note_dead_letter() -> int:
    """Record an unparseable ingest message; alert once the DLQ crosses the threshold."""
    settings = get_settings()
    count = coverage.record_dead_letter()
    if count >= settings.dead_letter_alert_threshold:
        _raise(rule_id="dead_letter_backlog", domain="audit", severity="critical",
               entity_id="ingest", message=f"{count} unparseable events in the dead-letter path",
               evidence={"dead_letters": count}, settings=settings)
    return count


def sweep_coverage() -> dict:
    """Flag sources silent beyond the heartbeat window (coverage blind spots)."""
    settings = get_settings()
    stale = coverage.stale_sources(now=_now(), max_min=settings.coverage_heartbeat_max_min)
    for s in stale:
        _raise(rule_id="coverage_heartbeat", domain="audit", severity="warning",
               entity_id=s["source"], message=f"No events from {s['source']} for "
               f"{s['silent_min']} min", evidence=s, settings=settings)
    return {"stale_sources": stale, "dead_letters": coverage.dead_letters()}


# ── Merkle-root anchoring (WORM tamper-evidence over ranges) ────────────────
def anchor_ledger(*, external_ref: str = "") -> dict | None:
    """Compute a Merkle root over the whole ledger and record a durable anchor (None if empty)."""
    entries = audit.all_entries()
    if not entries:
        return None
    root = merkle.build_root([e.entry_hash for e in entries])
    anchored_at = _now()
    audit.record_anchor(seq_from=entries[0].seq, seq_to=entries[-1].seq, merkle_root=root,
                        anchored_at=anchored_at, external_ref=external_ref)
    return {"seq_from": entries[0].seq, "seq_to": entries[-1].seq, "merkle_root": root,
            "anchored_at": anchored_at.isoformat(), "external_ref": external_ref}


def get_anchors() -> list[dict]:
    return audit.get_anchors()


def inclusion_proof(seq: int) -> dict | None:
    """Prove entry ``seq`` is committed by the latest anchor covering it (None if not anchored)."""
    entries = audit.all_entries()
    anchors = [a for a in audit.get_anchors() if a["seq_from"] <= seq <= a["seq_to"]]
    if not entries or not anchors:
        return None
    anchor = anchors[-1]
    covered = [e for e in entries if anchor["seq_from"] <= e.seq <= anchor["seq_to"]]
    leaves = [e.entry_hash for e in covered]
    index = next((i for i, e in enumerate(covered) if e.seq == seq), None)
    if index is None:
        return None
    proof = merkle.inclusion_proof(leaves, index)
    return {"seq": seq, "leaf": leaves[index], "proof": proof,
            "merkle_root": anchor["merkle_root"],
            "verified": merkle.verify_proof(leaves[index], proof, anchor["merkle_root"])}


# ── WORM mirror read-back ──────────────────────────────────────────────────
def worm_entry(seq: int) -> dict | None:
    from app import worm
    return worm.fetch(seq)


# ── Audit lifecycle: cold-storage tiering + retention purge (CEAAPSS-566) ───
def _contiguous_runs(entries: list) -> list[list]:
    """Split seq-sorted entries into contiguous runs (so each cold archive names an exact range)."""
    runs: list[list] = []
    for e in entries:
        if runs and e.seq == runs[-1][-1].seq + 1:
            runs[-1].append(e)
        else:
            runs.append([e])
    return runs


def audit_cold_tier() -> dict:
    """Archive hot entries older than the cold-age threshold to cold storage (cost + DR)."""
    settings = get_settings()
    if not coldstore.enabled():
        return {"archived": 0, "reason": "cold_store_disabled"}
    cutoff = _now() - timedelta(days=settings.audit_cold_age_days)
    already = coldstore.archived_seqs()
    aged = sorted((e for e in audit.all_entries()
                   if e.occurred_at < cutoff and e.seq not in already), key=lambda e: e.seq)
    for run in _contiguous_runs(aged):
        coldstore.archive(run)
    return {"archived": len(aged)}


def audit_purge() -> dict:
    """Purge aged entries from the hot ledger — but only a contiguous prefix that is past retention,
    already cold-archived (preserved), and Merkle-anchored (tamper-evidence retained)."""
    settings = get_settings()
    if not coldstore.enabled():
        return {"purged": 0, "reason": "cold_store_disabled"}
    audit_cold_tier()                       # ensure the aged range is preserved before any deletion
    cutoff = privacy._add_years(_now(), -settings.audit_retention_years)
    max_anchor = audit.max_anchored_seq()
    archived = coldstore.archived_seqs()
    wm = audit.purge_watermark()["purged_through_seq"]
    purge_to = wm
    for e in sorted(audit.all_entries(), key=lambda e: e.seq):
        if e.seq <= wm:
            continue
        if e.occurred_at < cutoff and e.seq <= max_anchor and e.seq in archived:
            purge_to = e.seq                # extend the contiguous purgeable prefix
        else:
            break
    if purge_to <= wm:
        return {"purged": 0, **audit.purge_watermark()}
    result = audit.purge_through(purge_to)
    # Record the purge on the bus (not the ledger — that would be circular + consume a seq); the
    # retained Merkle anchor + carry-hash watermark are the durable evidence of what was purged.
    events.publish(events.EVENT_SIGNAL, {
        "engine": settings.engine_id, "event": "audit_purged", "purged_through_seq": purge_to,
        "purged": result["purged"], "retained_anchor_seq": max_anchor})
    return {**result, "retained_anchor_seq": max_anchor}


def audit_lifecycle() -> dict:
    return {
        "cold_store": get_settings().cold_store_gateway,
        "cold_archived_seqs": len(coldstore.archived_seqs()),
        "hot_entries": len(audit.all_entries()),
        "max_anchored_seq": audit.max_anchored_seq(),
        **audit.purge_watermark(),
    }


def cold_entry(seq: int) -> dict | None:
    """Read a purged/archived record back from cold storage."""
    return coldstore.fetch(seq)


# ── Compliance dashboard (RAG per domain) ──────────────────────────────────
def dashboard() -> dict:
    """RAG status per domain: red = open critical case, amber = open warning, else green."""
    alerts = get_alert_repository().list()
    cases = [c for c in get_case_repository().list() if c.status in ("open", "investigating")]
    rag: dict[str, str] = {}
    for domain in DOMAINS:
        if any(c.domain == domain and c.severity == "critical" for c in cases):
            rag[domain] = "red"
        elif any(a.domain == domain and a.severity in ("warning", "critical") for a in alerts):
            rag[domain] = "amber"
        else:
            rag[domain] = "green"
    chain = audit.verify_chain()
    vals = rag.values()
    overall = "red" if "red" in vals else "amber" if "amber" in vals else "green"
    return {
        "rag": rag,
        "overall": overall,
        "open_cases": len(cases),
        "alerts": len(alerts),
        "audit_entries": chain["entries"],
        "audit_chain_verified": chain["verified"],
    }
