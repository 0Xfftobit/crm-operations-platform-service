"""Step scheduling (System Design §1/§4) — compute a step's next send time.

``next_send_at = clamp_to_legal(snap_to_best_time(now + delay))``. The base is ``now + step.delay``;
if the Timing Optimizing Engine gave a best-time hour we snap toward it, then everything is clamped
forward into the contact-local TCPA window (the guardrail re-checks at send time as the hard stop).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app import quiet_hours
from app.core.config import Settings
from app.domain.models import Contact


def _snap_to_best_time(when: datetime, contact: Contact, default_tz: str) -> datetime:
    """Move ``when`` to the contact's best-time hour (local) on or after ``when``, if provided."""
    if contact.best_time_hour is None:
        return when
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    for tz in (contact.timezone, default_tz, "UTC"):
        if not tz:
            continue
        try:
            zone = ZoneInfo(tz)
            break
        except (ZoneInfoNotFoundError, ValueError):
            continue
    else:
        return when
    local = when.astimezone(zone)
    target = local.replace(hour=contact.best_time_hour, minute=0, second=0, microsecond=0)
    if target < local:                            # best hour already passed today → tomorrow
        target = target + timedelta(days=1)
    return target.astimezone(when.tzinfo)


def next_send_at(now: datetime, delay: timedelta, contact: Contact, settings: Settings) -> datetime:
    """When the next step should fire: delay + best-time snap, clamped into the legal window."""
    base = _snap_to_best_time(now + delay, contact, settings.default_timezone)
    return quiet_hours.next_allowed(base, tz=contact.timezone, default_tz=settings.default_timezone,
                                    quiet_end=settings.quiet_hours_end,
                                    quiet_start=settings.quiet_hours_start)
