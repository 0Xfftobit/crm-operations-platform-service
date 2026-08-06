"""End-to-end cadence pipeline: enroll → tick → guardrail → send → branch/stop, plus the ACs."""
from datetime import UTC, datetime, timedelta

from app import providers, service
from app.domain.models import Contact, EngagementEvent, SequenceDef, Step

DAY = datetime(2026, 8, 4, 14, 0, tzinfo=UTC)      # 10:00 America/New_York — allowed window
NIGHT = datetime(2026, 8, 4, 7, 0, tzinfo=UTC)     # 03:00 NY — quiet hours


def _contact(cid="c1", **kw):
    base = {"name": "Ana", "property_name": "12 Oak St", "agent_name": "Lee",
            "timezone": "America/New_York", "consent_basis": "express_written",
            "email": "a@x.com", "phone": "+15551234567"}
    return service.upsert_contact(Contact(contact_id=cid, **{**base, **kw}))


def _seq(sid="seq", steps=None, stop_rules=None):
    steps = steps or [Step(id="s1", channel="sms", delay="PT0M", template="Hi {{lead_name}}")]
    return service.upsert_sequence(SequenceDef(
        sequence_id=sid, name=sid, version=1, steps=steps,
        stop_rules=stop_rules or ["replied", "converted", "opt_out"]))


# ── Personalization + send (AC: variables substitute, correct channel) ─────
def test_personalized_send_on_correct_channel():
    _contact()
    _seq(steps=[Step(id="s1", channel="sms", delay="PT0M",
                     template="Hi {{lead_name}} about {{property_name}} — {{agent_name}}")])
    service.enroll("c1", "seq", now=DAY)
    service.tick(now=DAY)
    assert providers.sent_log[-1]["channel"] == "sms"
    assert providers.sent_log[-1]["body"] == "Hi Ana about 12 Oak St — Lee"   # AC substitution


def test_unknown_variable_renders_blank_not_token():
    _contact()
    _seq(steps=[Step(id="s1", channel="email", template="Hi {{lead_name}} {{missing}}")])
    service.enroll("c1", "seq", now=DAY)
    service.tick(now=DAY)
    assert providers.sent_log[-1]["body"] == "Hi Ana "         # {{missing}} → blank, never leaked


# ── Guardrails end-to-end (AC: no send in quiet hours) ─────────────────────
def test_no_send_in_quiet_hours_then_sends_when_allowed():
    _contact()
    _seq(steps=[Step(id="s1", channel="sms", template="Hi")])
    service.enroll("c1", "seq", now=NIGHT)                    # scheduler pre-clamps to daytime
    service.tick(now=NIGHT)
    assert providers.sent_log == []                          # nothing sent during quiet hours (AC)
    service.tick(now=DAY)                                     # daytime → sends
    assert len(providers.sent_log) == 1



def test_unknown_consent_fails_closed():
    _contact(consent_basis="unknown")
    _seq(steps=[Step(id="s1", channel="sms", template="Hi")])
    service.enroll("c1", "seq", now=DAY)
    service.tick(now=DAY)
    assert providers.sent_log == []                          # never sent without consent


def test_dnc_contact_stopped_not_sent():
    _contact(dnc=True)
    _seq(steps=[Step(id="s1", channel="sms", template="Hi")])
    service.enroll("c1", "seq", now=DAY)
    # enrollment refused up front for a suppressed contact
    r = service.enroll("c1", "seq", now=DAY)
    assert r["status"] == "blocked"


# ── Stop conditions (AC: reply pauses, opt-out permanent, convert exits) ───
def test_reply_pauses_sequence():
    _contact()
    _seq(steps=[Step(id="s1", channel="email", template="Hi"),
                Step(id="s2", channel="email", delay="PT48H", template="Follow up")])
    r = service.enroll("c1", "seq", now=DAY)
    eid = r["enrollment"]["enrollment_id"]
    service.tick(now=DAY)
    service.ingest_engagement(EngagementEvent(contact_id="c1", enrollment_id=eid, kind="reply"))
    assert service.get_enrollment(eid)["state"] == "paused"
    assert service.get_enrollment(eid)["stopped_reason"] == "replied"


def test_opt_out_is_permanent_and_blocks_reenroll():
    _contact()
    _seq(steps=[Step(id="s1", channel="sms", template="Hi")])
    service.enroll("c1", "seq", now=DAY)
    service.opt_out("c1")
    from app.repositories import get_contact_repository
    assert get_contact_repository().get("c1").opted_out is True
    # active enrollment stopped + a re-enroll is blocked (permanent)
    assert all(e["state"] == "stopped" for e in service.list_enrollments(contact_id="c1"))
    assert service.enroll("c1", "seq", now=DAY)["status"] == "blocked"


def test_convert_exits_sequence():
    _contact()
    _seq(steps=[Step(id="s1", channel="email", template="Hi"),
                Step(id="s2", channel="email", delay="PT48H", template="More")])
    r = service.enroll("c1", "seq", now=DAY)
    service.record_stage_change("c1", "application")
    assert service.get_enrollment(r["enrollment"]["enrollment_id"])["state"] == "stopped"
    assert service.get_enrollment(r["enrollment"]["enrollment_id"])["stopped_reason"] == "converted"


# ── Idempotency + frequency cap (AC) ───────────────────────────────────────
def test_duplicate_enrollment_no_duplicate():
    _contact()
    _seq(steps=[Step(id="s1", channel="sms", template="Hi")])
    a = service.enroll("c1", "seq", now=DAY)
    b = service.enroll("c1", "seq", now=DAY)                  # replay → no new enrollment
    assert a["status"] == "enrolled" and b["status"] == "duplicate"
    assert len(service.list_enrollments(contact_id="c1")) == 1


def test_global_frequency_cap_across_sends():
    _contact()
    _seq(sid="fast", steps=[Step(id=f"s{i}", channel="sms", delay="PT0M", template="m")
                            for i in range(4)], stop_rules=[])
    service.enroll("c1", "fast", now=DAY)
    for _ in range(12):
        service.tick(now=DAY)
    assert len(providers.sent_log) == 2                      # freq_cap_day default = 2
    from app.repositories import get_ledger_repository
    assert any(r.block_reason == "frequency_cap" for r in get_ledger_repository().list())


def test_max_steps_completes():
    _contact()
    _seq(sid="cap2", steps=[Step(id="s1", channel="sms", template="a"),
                            Step(id="s2", channel="email", template="b"),
                            Step(id="s3", channel="sms", template="c")],
         stop_rules=["max_steps:2"])
    r = service.enroll("c1", "cap2", now=DAY)
    for _ in range(8):
        service.tick(now=DAY)
    e = service.get_enrollment(r["enrollment"]["enrollment_id"])
    assert e["state"] == "completed" and e["stopped_reason"] == "max_steps"


# ── Branching (AC: opened → Path A) ────────────────────────────────────────
def test_branch_opened_takes_path_a():
    _contact()
    _seq(sid="br", steps=[
        Step(id="s1", channel="email", template="intro",
             branches=[{"if": "opened", "goto": "s3"},
                       {"if": "no_open_after:PT48H", "goto": "s2"}]),
        Step(id="s2", channel="sms", template="path B nudge"),
        Step(id="s3", channel="email", template="path A value"),
    ], stop_rules=[])
    r = service.enroll("c1", "br", now=DAY)
    eid = r["enrollment"]["enrollment_id"]
    service.tick(now=DAY)                                     # sends s1
    service.ingest_engagement(EngagementEvent(contact_id="c1", enrollment_id=eid, step_id="s1",
                                              kind="open"))
    service.tick(now=DAY)                                     # branch → opened → s3
    service.tick(now=DAY)                                     # sends s3
    channels = [s["channel"] for s in providers.sent_log]
    assert channels == ["email", "email"]                    # s1 + s3 (Path A), never the SMS s2


def test_branch_no_open_takes_path_b_after_window():
    _contact()
    _seq(sid="br2", steps=[
        Step(id="s1", channel="email", template="intro",
             branches=[{"if": "opened", "goto": "s3"},
                       {"if": "no_open_after:PT48H", "goto": "s2"}]),
        Step(id="s2", channel="sms", template="path B"),
        Step(id="s3", channel="email", template="path A"),
    ], stop_rules=[])
    service.enroll("c1", "br2", now=DAY)
    service.tick(now=DAY)                                     # sends s1
    service.tick(now=DAY)                                     # branch: not opened, <48h → waiting
    assert [s["channel"] for s in providers.sent_log] == ["email"]
    later = DAY + timedelta(hours=49)
    service.tick(now=later)                                  # 48h passed → path B (sms)
    service.tick(now=later)
    assert providers.sent_log[-1]["channel"] == "sms"


# ── Triggers + analytics ───────────────────────────────────────────────────
def test_lead_score_trigger_threshold():
    _contact()
    _seq(steps=[Step(id="s1", channel="sms", template="Hi")])
    assert service.handle_lead_scored("c1", 40.0, "seq")["status"] == "skipped"   # below 70
    assert service.handle_lead_scored("c1", 85.0, "seq")["status"] == "enrolled"


def test_analytics_per_step_rates():
    _contact()
    _seq(sid="an", steps=[Step(id="s1", channel="email", template="Hi")], stop_rules=[])
    r = service.enroll("c1", "an", now=DAY)
    eid = r["enrollment"]["enrollment_id"]
    service.tick(now=DAY)
    service.ingest_engagement(EngagementEvent(contact_id="c1", enrollment_id=eid, step_id="s1",
                                              kind="open"))
    from app import analytics
    a = analytics.sequence_analytics("an")
    s1 = next(s for s in a["steps"] if s["step_id"] == "s1")
    assert s1["sent"] == 1 and s1["open_rate"] == 1.0
