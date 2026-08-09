"""Guardrail Gateway (System Architecture §3/§8, NFR guardrail-correctness) — the single choke.

**Nothing sends without passing here, and it fails closed.** Before any outreach the gateway checks
(in order): suppression → consent → cooldown → quiet hours → global frequency cap → per-sequence
frequency cap. A failure resolves to one of ``stop`` / ``hold`` / ``send`` dispositions. Unknown
consent always maps to ``hold``, so a missing consent basis can never produce a violation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app import quiet_hours
from app.constants import VALID_CONSENT
from app.core.config import Settings
from app.domain.models import Contact
from app.frequency import FrequencyStore


@dataclass
class Decision:
    disposition: str          # send | hold | stop
    reason: str = ""          # guardrail block reason (constants.GUARDRAIL_BLOCKS) or ""
    retry_at: datetime | None = None

    @property
    def allowed(self) -> bool:
        return self.disposition == "send"


def check(
    contact: Contact,
    *,
    sequence_id: str,
    now: datetime,
    settings: Settings,
    freq: FrequencyStore,
    per_seq_day: int | None = None,
    per_seq_week: int | None = None,
) -> Decision:
    """Fail-closed pre-send evaluation for a contact at ``now``."""
    # 1. Permanent suppression — terminal.
    if contact.opted_out:
        return Decision("stop", "opted_out")
    if contact.dnc:
        return Decision("stop", "dnc")
    if contact.suppressed:
        return Decision("stop", f"suppressed:{contact.suppressed_reason or 'provider'}")

    # 2. Consent — fail closed if unknown/insufficient (hold, never send).
    if contact.consent_basis not in VALID_CONSENT:
        return Decision("hold", "consent_unknown",
                        now + timedelta(seconds=settings.consent_cache_ttl_s))

    # 3. Manual-outreach cooldown — pause automation after an LO personally reached out.
    if contact.last_manual_outreach_at is not None:
        cooldown_until = contact.last_manual_outreach_at + timedelta(
            hours=settings.manual_outreach_cooldown_h)
        if now < cooldown_until:
            return Decision("hold", "manual_cooldown", cooldown_until)

    # 4. TCPA quiet hours (contact-local timezone) — clamp forward to the next legal window.
    if not quiet_hours.is_allowed(now, tz=contact.timezone, default_tz=settings.default_timezone,
                                  quiet_end=settings.quiet_hours_end,
                                  quiet_start=settings.quiet_hours_start):
        retry = quiet_hours.next_allowed(now, tz=contact.timezone,
                                         default_tz=settings.default_timezone,
                                         quiet_end=settings.quiet_hours_end,
                                         quiet_start=settings.quiet_hours_start)
        return Decision("hold", "quiet_hours", retry)

    # 5. Global + per-sequence frequency cap — day + week.
    remaining = freq.remaining(
        contact.tenant_id,
        contact.contact_id,
        sequence_id,
        now,
        cap_day=settings.freq_cap_day,
        cap_week=settings.freq_cap_week,
        per_seq_day=per_seq_day,
        per_seq_week=per_seq_week,
    )
    if remaining <= 0:
        # retry at the next legal window tomorrow (day counter resets).
        tomorrow = quiet_hours.next_allowed(
            now + timedelta(days=1), tz=contact.timezone, default_tz=settings.default_timezone,
            quiet_end=settings.quiet_hours_end, quiet_start=settings.quiet_hours_start)
        return Decision("hold", "frequency_cap", tomorrow)

    return Decision("send")
