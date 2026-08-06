"""Pre-send compliance hook (FR-002/003/004/017): fail-closed TCPA/opt-out/DNC/CAN-SPAM."""
from datetime import UTC, datetime

from app import precheck, service
from app.core.config import get_settings
from app.domain.models import Consent, PrecheckRequest
from app.repositories import get_consent_repository


def _consent(**kw) -> Consent:
    base = dict(lead_id="lead_1", channel="sms", opted_in=True, timezone="UTC")
    base.update(kw)
    return Consent(**base)


def test_missing_consent_is_denied_fail_closed():
    req = PrecheckRequest(channel="sms", contact_id="unknown")
    result = service.run_precheck(req)
    assert result.decision == "deny"
    assert "no_consent" in result.reasons
    assert result.audit_seq is not None            # every decision is audited


def test_opt_out_and_dnd_block():
    get_consent_repository().put(_consent(opted_in=False))
    r1 = service.run_precheck(PrecheckRequest(channel="sms", contact_id="lead_1"))
    assert r1.decision == "deny" and "opt_out" in r1.reasons

    get_consent_repository().put(_consent(lead_id="lead_2", opted_in=True, on_dnd_registry=True))
    r2 = service.run_precheck(PrecheckRequest(channel="sms", contact_id="lead_2"))
    assert r2.decision == "deny" and "dnd_registry" in r2.reasons


def test_allow_when_consented():
    get_consent_repository().put(_consent())
    r = service.run_precheck(PrecheckRequest(channel="sms", contact_id="lead_1"))
    assert r.decision == "allow" and r.reasons == []


def test_quiet_hours_violation_in_contact_timezone():
    settings = get_settings()
    # 03:00 UTC on a winter day is 22:00 in New York (EST) → inside 21:00–08:00 quiet window.
    when = datetime(2026, 1, 15, 3, 0, tzinfo=UTC)
    req = PrecheckRequest(channel="sms", contact_id="lead_1", scheduled_for=when)
    reasons = precheck.evaluate(req, _consent(timezone="America/New_York"), settings=settings)
    assert "quiet_hours_violation" in reasons
    # Same instant is 12:00 in Singapore → allowed.
    ok = precheck.evaluate(req, _consent(timezone="Asia/Singapore"), settings=settings)
    assert "quiet_hours_violation" not in ok


def test_can_spam_requires_unsubscribe_and_sender():
    settings = get_settings()
    req = PrecheckRequest(channel="email", contact_id="lead_1", has_unsubscribe=False, sender_id="")
    reasons = precheck.evaluate(req, _consent(channel="email"), settings=settings)
    assert "missing_unsubscribe" in reasons and "missing_sender_identity" in reasons

    good = PrecheckRequest(channel="email", contact_id="lead_1", has_unsubscribe=True,
                           sender_id="CR Equity <noreply@crequity.ai>")
    assert precheck.evaluate(good, _consent(channel="email"), settings=settings) == []


def test_blocked_send_raises_a_marketing_alert():
    from app.repositories import get_alert_repository
    service.run_precheck(PrecheckRequest(channel="sms", contact_id="nobody"))
    alerts = get_alert_repository().list()
    assert any(a.domain == "marketing" and a.severity == "warning" for a in alerts)
