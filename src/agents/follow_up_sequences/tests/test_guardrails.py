"""Fail-closed guardrail gateway + quiet-hours + frequency cap + duration/branch primitives."""
from datetime import UTC, datetime, timedelta

from app import branching, guardrails, quiet_hours
from app.core.config import Settings
from app.domain.models import Contact, Step
from app.duration import condition_duration, parse_duration
from app.frequency import FrequencyStore

S = Settings()
SEQ = "seq-1"


def _contact(**kw) -> Contact:
    base = {"contact_id": "c", "timezone": "America/New_York", "consent_basis": "express_written"}
    return Contact(**{**base, **kw})


# NY is UTC-4 in August: 8am NY = 12:00 UTC, 9pm NY = 01:00 UTC (next day).
DAY = datetime(2026, 8, 4, 14, 0, tzinfo=UTC)     # 10:00 NY — allowed
NIGHT = datetime(2026, 8, 4, 7, 0, tzinfo=UTC)     # 03:00 NY — quiet hours


# ── Quiet hours (TCPA, contact-local) ──────────────────────────────────────
def test_quiet_hours_allows_daytime_blocks_night():
    assert quiet_hours.is_allowed(DAY, tz="America/New_York") is True
    assert quiet_hours.is_allowed(NIGHT, tz="America/New_York") is False


def test_next_allowed_clamps_forward():
    nxt = quiet_hours.next_allowed(NIGHT, tz="America/New_York")
    assert quiet_hours.is_allowed(nxt, tz="America/New_York") is True
    assert nxt > NIGHT


def test_unknown_timezone_falls_back():
    # empty tz → default tz used, still evaluated (fail-closed, not "assume allowed")
    assert isinstance(quiet_hours.is_allowed(DAY, tz="", default_tz="America/New_York"), bool)


# ── Guardrail dispositions ─────────────────────────────────────────────────
def test_guardrail_passes_when_all_clear():
    d = guardrails.check(_contact(), sequence_id=SEQ, now=DAY, settings=S, freq=FrequencyStore())
    assert d.disposition == "send" and d.allowed


def test_guardrail_stops_on_opt_out_and_dnc():
    assert guardrails.check(_contact(opted_out=True), sequence_id=SEQ, now=DAY, settings=S,
                            freq=FrequencyStore()).disposition == "stop"
    assert guardrails.check(_contact(dnc=True), sequence_id=SEQ, now=DAY, settings=S,
                            freq=FrequencyStore()).reason == "dnc"


def test_guardrail_fail_closed_on_unknown_consent():
    d = guardrails.check(_contact(consent_basis="unknown"), sequence_id=SEQ, now=DAY, settings=S,
                         freq=FrequencyStore())
    assert d.disposition == "hold" and d.reason == "consent_unknown"


def test_guardrail_holds_in_quiet_hours():
    d = guardrails.check(_contact(), sequence_id=SEQ, now=NIGHT, settings=S, freq=FrequencyStore())
    assert d.disposition == "hold" and d.reason == "quiet_hours"
    assert quiet_hours.is_allowed(d.retry_at, tz="America/New_York") is True


def test_guardrail_holds_on_frequency_cap():
    freq = FrequencyStore()
    for _ in range(S.freq_cap_day):
        freq.increment("default", "c", SEQ, DAY)
    d = guardrails.check(_contact(), sequence_id=SEQ, now=DAY, settings=S, freq=freq)
    assert d.disposition == "hold" and d.reason == "frequency_cap"


def test_guardrail_holds_on_per_sequence_frequency_cap():
    freq = FrequencyStore()
    seq_day = 1
    for _ in range(seq_day):
        freq.increment("default", "c", SEQ, DAY)
    d = guardrails.check(
        _contact(), sequence_id=SEQ, now=DAY, settings=S, freq=freq,
        per_seq_day=seq_day, per_seq_week=10,
    )
    assert d.disposition == "hold" and d.reason == "frequency_cap"


def test_guardrail_manual_cooldown():
    c = _contact(last_manual_outreach_at=DAY - timedelta(hours=2))
    d = guardrails.check(c, sequence_id=SEQ, now=DAY, settings=S, freq=FrequencyStore())
    assert d.disposition == "hold" and d.reason == "manual_cooldown"


# ── Duration + branching ───────────────────────────────────────────────────
def test_parse_duration():
    assert parse_duration("PT48H") == timedelta(hours=48)
    assert parse_duration("P1D") == timedelta(days=1)
    assert parse_duration("PT0M") == timedelta(0)
    assert parse_duration("garbage") == timedelta(0)
    assert condition_duration("no_open_after:PT48H") == timedelta(hours=48)


def test_branch_opened_goes_path_a():
    step = Step(id="s2", branches=[{"if": "opened", "goto": "s3"},
                                   {"if": "no_open_after:PT48H", "goto": "s4"}])
    got = branching.next_step_from_branches(step, engaged={"open"}, elapsed=timedelta(hours=1))
    assert got == "s3"


def test_branch_no_open_after_window_goes_path_b():
    step = Step(id="s2", branches=[{"if": "opened", "goto": "s3"},
                                   {"if": "no_open_after:PT48H", "goto": "s4"}])
    # not opened + 48h elapsed → path B; but before 48h → no branch yet
    early = branching.next_step_from_branches(step, engaged=set(), elapsed=timedelta(hours=1))
    assert early is None
    late = branching.next_step_from_branches(step, engaged=set(), elapsed=timedelta(hours=49))
    assert late == "s4"


def test_branch_wait_is_longest_timed_condition():
    step = Step(id="s2", branches=[{"if": "no_open_after:PT48H", "goto": "s4"}])
    assert branching.branch_wait(step) == timedelta(hours=48)
