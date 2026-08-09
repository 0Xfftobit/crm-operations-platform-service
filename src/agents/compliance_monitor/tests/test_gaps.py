"""Coverage for the second-increment gap features: Engine-8 policy ingestion (FR-014), PII
detection (FR-007), data retention + right-to-erasure (Data Privacy), regulatory-change monitoring
(CEAAPSS-565), alert dedup (FR-019), batch geo-disparity (FR-015), integrity check (FR-018),
and PDF export (CEAAPSS-566)."""
from datetime import UTC, datetime, timedelta

from app import audit, pdf, service
from app.domain.models import (
    ActivityEvent,
    CompliancePolicy,
    Consent,
    PrecheckRequest,
    Regulation,
    RetentionRecord,
)
from app.repositories import (
    get_alert_repository,
    get_consent_repository,
    get_regulation_repository,
    get_retention_repository,
)


# ── FR-014 Engine-8 policy ingestion ───────────────────────────────────────
def test_policy_overrides_fairness_band():
    # Tighten the 4/5ths floor to 0.95 via policy; a 0.90 ratio now fails.
    service.ingest_policy(CompliancePolicy(version=1, thresholds={"fairness_ratio_min": 0.95},
                                           signed_by="engine-8"))
    # ratio 36/40 = 0.90, which passes the 0.80 default but fails the 0.95 policy floor.
    cohorts = {"a": {"selected": 40, "total": 100}, "b": {"selected": 36, "total": 100}}
    r = service.evaluate_fairness("lead_assignment", "cohort", cohorts)
    assert r.passes is False


def test_policy_highest_version_wins():
    service.ingest_policy(CompliancePolicy(version=2, thresholds={"adverse_action_days": 15}))
    stale = service.ingest_policy(
        CompliancePolicy(version=1, thresholds={"adverse_action_days": 99}))
    assert stale["active"] is False
    assert service.active_policy().version == 2


# ── FR-007 PII detection ───────────────────────────────────────────────────
def test_pii_in_outbound_content_blocks_precheck():
    get_consent_repository().put(Consent(lead_id="lead_1", channel="sms", opted_in=True))
    req = PrecheckRequest(channel="sms", contact_id="lead_1",
                          content="Your SSN 123-45-6789 is on file")
    result = service.run_precheck(req)
    assert result.decision == "deny"
    assert any(r.startswith("pii_exposure") for r in result.reasons)


def test_pii_in_activity_raises_data_privacy_alert():
    service.record_activity(ActivityEvent(
        event_type="record_change", engine="crm", entity_id="lead_2",
        reasoning="card 4111 1111 1111 1111 captured"))
    assert any(a.domain == "data_privacy" and a.rule_id == "pii_exposure"
               for a in get_alert_repository().list())


# ── Data Privacy: retention + erasure ──────────────────────────────────────
def test_retention_sweep_warns_then_anonymizes():
    now = datetime.now(UTC)
    repo = get_retention_repository()
    repo.put(RetentionRecord(lead_id="warn", retained_until=now + timedelta(days=3)))
    repo.put(RetentionRecord(lead_id="due", retained_until=now - timedelta(days=1)))
    states = {s["lead_id"]: s["state"] for s in service.check_retention()}
    assert states["warn"] == "warning"
    assert states["due"] == "anonymized"        # auto-anonymized past deadline
    assert get_retention_repository().get("due").anonymized is True


def test_retention_days_left_is_fractional():
    now = datetime.now(UTC)
    repo = get_retention_repository()
    repo.put(RetentionRecord(lead_id="half_day", retained_until=now + timedelta(hours=12)))
    states = {s["lead_id"]: s for s in service.check_retention()}
    assert states["half_day"]["state"] == "warning"
    assert 0.4 < states["half_day"]["days_left"] < 0.6


def test_add_years_respects_leap_day():
    from app import privacy
    leap = datetime(2024, 2, 29, tzinfo=UTC)
    # Feb 29 cannot exist in 2023 or 2025, so it rolls to Mar 1.
    assert privacy._add_years(leap, -1) == datetime(2023, 3, 1, tzinfo=UTC)
    assert privacy._add_years(leap, 1) == datetime(2025, 3, 1, tzinfo=UTC)
    normal = datetime(2023, 3, 1, tzinfo=UTC)
    assert privacy._add_years(normal, 1) == datetime(2024, 3, 1, tzinfo=UTC)


def test_right_to_erasure_anonymizes_consent():
    get_consent_repository().put(Consent(lead_id="lead_9", channel="email", opted_in=True))
    rec = service.erase_lead("lead_9")
    assert rec.erased is True
    assert get_consent_repository().get("lead_9", "email").opted_in is False


# ── CEAAPSS-565 regulatory-change monitoring ───────────────────────────────
def test_regulation_change_flags_and_alerts():
    get_regulation_repository().put(Regulation(reg_id="tcpa", name="TCPA", authority="FCC"))
    reg = service.report_regulatory_change("tcpa", summary="new quiet-hours ruling")
    assert reg.status == "review_needed"
    assert any(a.domain == "regulatory" and a.rule_id == "regulation_changed"
               for a in get_alert_repository().list())


def test_stale_regulation_sweep():
    old = datetime.now(UTC) - timedelta(days=400)
    get_regulation_repository().put(Regulation(reg_id="rera", name="RERA", last_reviewed=old))
    assert "rera" in service.sweep_regulations(max_age_days=180)


# ── FR-019 alert dedup ─────────────────────────────────────────────────────
def test_repeat_warning_is_suppressed_but_criticals_are_not():
    a1 = service._raise(rule_id="x", domain="marketing", severity="warning", entity_id="e1",
                        settings=service._effective())
    a2 = service._raise(rule_id="x", domain="marketing", severity="warning", entity_id="e1",
                        settings=service._effective())
    assert a1 is not None and a2 is None        # second suppressed inside the window
    c1 = service._raise(rule_id="y", domain="ai_fairness", severity="critical", entity_id="e2",
                        settings=service._effective())
    c2 = service._raise(rule_id="y", domain="ai_fairness", severity="critical", entity_id="e2",
                        settings=service._effective())
    assert c1 is not None and c2 is not None     # criticals never suppressed


# ── FR-015 batch geo-disparity ─────────────────────────────────────────────
def test_batch_flags_redlining():
    out = service.evaluate_fairness_batch([{
        "sub_agent": "lead_scoring", "cohort_dimension": "geo_proxy",
        "cohorts": {"a": {"selected": 42, "total": 100}, "b": {"selected": 31, "total": 100}}}])
    assert out["redlining_risk"] == ["lead_scoring"]


# ── FR-018 integrity check ─────────────────────────────────────────────────
def test_integrity_check_alerts_on_tamper():
    audit.append(event_id="e", event_type="decision", actor_type="engine", entity_id="l")
    audit.append(event_id="e2", event_type="decision", actor_type="engine", entity_id="l")
    assert service.integrity_check()["verified"] is True
    audit.all_entries()[0].outcome = "TAMPERED"
    res = service.integrity_check()
    assert res["verified"] is False
    assert any(a.rule_id == "audit_chain_break" and a.severity == "critical"
               for a in get_alert_repository().list())


# ── CEAAPSS-566 PDF export ─────────────────────────────────────────────────
def test_pdf_export_is_valid_pdf():
    audit.append(event_id="e", event_type="comm_sent", actor_type="engine", entity_id="l")
    blob = pdf.render(audit.all_entries())
    assert blob.startswith(b"%PDF-1.4")
    assert blob.rstrip().endswith(b"%%EOF")


def test_pdf_export_over_http(client):
    audit.append(event_id="e", event_type="comm_sent", actor_type="engine", entity_id="l")
    resp = client.get("/api/v1/audit/export?fmt=pdf", headers={"X-Role": "auditor"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
