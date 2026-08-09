"""Data-privacy engine — retention enforcement + GDPR/PDPA right-to-erasure (Data Privacy domain).

Retention: each lead's data is registered with a ``retained_until`` deadline. A sweep warns before
the deadline (pre-deletion alert) and auto-anonymizes once past it. Erasure: an explicit request
anonymizes the lead's consent records and marks the data removed (incl. from training datasets) —
a pure-function core over the injected repositories; the service layer audits + alerts.
"""
from datetime import datetime, timedelta

from app.core.config import Settings
from app.domain.models import RetentionRecord
from app.repositories import ConsentRepository, RetentionRepository


def _add_years(dt: datetime, years: int) -> datetime:
    """Add years to a datetime, accounting for leap-year edge cases."""
    try:
        return dt.replace(year=dt.year + years)
    except ValueError:
        # Feb 29 → Mar 1 on non-leap years.
        return dt.replace(year=dt.year + years, month=3, day=1)


def _default_retention_until(settings: Settings, now: datetime) -> datetime:
    if settings.data_retention_days > 0:
        return now + timedelta(days=settings.data_retention_days)
    return _add_years(now, settings.data_retention_years)


def register(
    repo: RetentionRepository, *, lead_id: str, category: str = "lead_data",
    settings: Settings, now: datetime, retained_until: datetime | None = None,
) -> RetentionRecord:
    rec = RetentionRecord(
        lead_id=lead_id, category=category,
        retained_until=retained_until or _default_retention_until(settings, now))
    repo.put(rec)
    return rec


def retention_state(rec: RetentionRecord, *, settings: Settings, now: datetime) -> dict:
    """{state, days_left}: anonymized | due (past deadline) | warning | ok."""
    if rec.anonymized or rec.erased:
        return {"state": "anonymized", "days_left": None}
    days_left = (rec.retained_until - now).total_seconds() / 86400
    if now >= rec.retained_until:
        return {"state": "due", "days_left": days_left}
    if days_left <= settings.retention_warn_days:
        return {"state": "warning", "days_left": days_left}
    return {"state": "ok", "days_left": days_left}


def anonymize(
    retention_repo: RetentionRepository, consent_repo: ConsentRepository, *,
    lead_id: str, now: datetime, erased: bool = False, reason: str = "",
) -> RetentionRecord:
    """Anonymize a lead: scrub consent identifiers + flag the retention record.

    ``erased=True`` marks a right-to-erasure request (removed from training datasets too).
    """
    for c in [c for c in consent_repo.list() if c.lead_id == lead_id]:
        c.opted_in = False           # no further contact against anonymized data
        c.timezone = "UTC"
        c.updated_at = now
        consent_repo.put(c)
    rec = retention_repo.get(lead_id) or RetentionRecord(lead_id=lead_id, retained_until=now)
    rec.anonymized = True
    rec.erased = erased
    rec.erasure_reason = reason
    rec.updated_at = now
    retention_repo.put(rec)
    return rec
