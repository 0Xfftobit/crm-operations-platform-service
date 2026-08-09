"""Event contract — consumed triggers + produced outreach signals (System Architecture §6).

Consumes ``lead.scored`` / ``deal.stage_changed`` / ``contact.replied`` / ``consent.updated`` +
channel webhooks to enroll and advance contacts; produces ``sequence.enrolled`` / ``outreach.sent``
/ ``outreach.failed`` / ``sequence.stopped`` / ``sequence.completed`` for analytics + forecasting +
Engine-8 compliance audit. Schema-invalid inputs go to a dead-letter log (§8). Kept in-process; the
real Kafka bus + Temporal workflows are deferred infra behind this interface.
"""
from datetime import UTC, datetime

import structlog

logger = structlog.get_logger(__name__)

# consumed
EVENT_LEAD_SCORED = "lead.scored"
EVENT_STAGE_CHANGED = "deal.stage_changed"
EVENT_REPLIED = "contact.replied"
EVENT_CONSENT_UPDATED = "consent.updated"
# produced
EVENT_ENROLLED = "sequence.enrolled"
EVENT_SENT = "outreach.sent"
EVENT_FAILED = "outreach.failed"
EVENT_STOPPED = "sequence.stopped"
EVENT_COMPLETED = "sequence.completed"
EVENT_SUPPRESSED = "contact.suppressed"

_events: list[dict] = []
_dlq: list[dict] = []


def publish(event_type: str, payload: dict) -> None:
    _events.append({"type": event_type, "payload": payload, "at": datetime.now(UTC).isoformat()})
    logger.info("event", event_type=event_type)


def dead_letter(payload: dict, *, reason: str) -> None:
    _dlq.append({"payload": payload, "reason": reason, "at": datetime.now(UTC).isoformat()})
    logger.warning("dead_letter", reason=reason)


def get_events(*, event_type: str | None = None, limit: int = 200) -> list[dict]:
    items = [e for e in _events if event_type is None or e["type"] == event_type]
    return list(reversed(items[-limit:]))


def get_dlq(*, limit: int = 200) -> list[dict]:
    return list(reversed(_dlq[-limit:]))


def reset_events() -> None:
    _events.clear()
    _dlq.clear()
