"""Pre-send compliance hook — the synchronous, **fail-closed** hard-rule gate (FR-002/003/004/017).

Runs only fast deterministic rules against cached consent: TCPA quiet-hours (contact-local time),
opt-out, DND/DNC registry, and CAN-SPAM (unsubscribe + sender identity on email). Any hit → ``deny``
with reasons; a missing consent record is treated as **no consent** (deny), never a silent allow.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.domain.models import Consent, PrecheckRequest


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_quiet_hours(when: datetime, tz: str, start: str, end: str) -> bool:
    try:
        local = when.astimezone(ZoneInfo(tz))
    except Exception:  # noqa: BLE001 — bad tz → evaluate in the given clock
        local = when
    cur, s, e = local.hour * 60 + local.minute, _minutes(start), _minutes(end)
    if s == e:
        return False
    return s <= cur < e if s < e else (cur >= s or cur < e)   # wraps midnight


def evaluate(req: PrecheckRequest, consent: Consent | None, *, settings: Settings) -> list[str]:
    """Return the list of hard-rule violations (empty = allow)."""
    reasons: list[str] = []
    if consent is None:
        reasons.append("no_consent")                     # fail-closed
    else:
        if not consent.opted_in:
            reasons.append("opt_out")
        if consent.on_dnd_registry and req.channel in ("sms", "call", "whatsapp"):
            reasons.append("dnd_registry")
        if req.scheduled_for is not None and in_quiet_hours(
                req.scheduled_for, consent.timezone,
                settings.quiet_hours_start, settings.quiet_hours_end):
            reasons.append("quiet_hours_violation")
    if req.channel == "email":                            # CAN-SPAM
        if not req.has_unsubscribe:
            reasons.append("missing_unsubscribe")
        if not req.sender_id:
            reasons.append("missing_sender_identity")
    return reasons
