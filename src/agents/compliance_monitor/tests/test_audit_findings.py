"""Regression tests for the gaps found in the 'find missed part' audit:
cache staleness on erasure, Kafka produce from sync threads, event idempotency,
adverse-action auto-trigger, consent.updated ingestion, and the missing-explainability case.
"""
import asyncio

from app import cache, kafka_bus, service
from app.domain.models import ActivityEvent, Consent, PrecheckRequest
from app.repositories import get_case_repository, get_disclosure_repository


# ── Erasure must not leave a stale opted-in consent in the cache ────────────
def test_erasure_flushes_the_consent_cache():
    service.upsert_consent(Consent(lead_id="l1", channel="sms", opted_in=True))
    # Warm the cache via a precheck (would allow).
    assert service.run_precheck(PrecheckRequest(channel="sms", contact_id="l1")).decision == "allow"
    assert cache.get("l1", "sms") is not None            # cached opted-in
    service.erase_lead("l1")
    # Cache must have been invalidated so the very next send is blocked.
    assert cache.get("l1", "sms") is None
    assert service.run_precheck(PrecheckRequest(channel="sms", contact_id="l1")).decision == "deny"


def test_retention_anonymize_flushes_cache():
    from datetime import UTC, datetime, timedelta

    from app.domain.models import RetentionRecord
    service.upsert_consent(Consent(lead_id="l2", channel="sms", opted_in=True))
    service.run_precheck(PrecheckRequest(channel="sms", contact_id="l2"))   # warm cache
    get_disclosure_repository()  # noqa
    from app.repositories import get_retention_repository
    get_retention_repository().put(
        RetentionRecord(lead_id="l2", retained_until=datetime.now(UTC) - timedelta(days=1)))
    service.check_retention()
    assert cache.get("l2", "sms") is None                # flushed on auto-anonymize


# ── Kafka producing from a worker thread (sync FastAPI handler) ─────────────
def test_produce_sync_works_off_the_main_loop():
    sent = []

    class _Fake:
        async def send_and_wait(self, topic, value):
            import json
            sent.append((topic, json.loads(value)))

    async def _scenario():
        kafka_bus._producer = _Fake()
        kafka_bus._loop = asyncio.get_running_loop()
        # Emulate a sync FastAPI handler: no running loop in this worker thread.
        await asyncio.to_thread(kafka_bus.produce_sync, "compliance.alert", {"x": 1})
        await asyncio.sleep(0.05)      # let the scheduled coroutine run on the main loop

    try:
        asyncio.run(_scenario())
    finally:
        kafka_bus._producer = None
        kafka_bus._loop = None
    assert sent == [("compliance.alert", {"x": 1})]


# ── Event idempotency (dedup by event_id, §8) ──────────────────────────────
def test_duplicate_event_id_is_deduped():
    first = service.record_activity(ActivityEvent(event_id="evt-1", event_type="comm_sent",
                                                  entity_id="l"))
    dup = service.record_activity(ActivityEvent(event_id="evt-1", event_type="comm_sent",
                                                entity_id="l"))
    assert first.get("audit_seq") == 1
    assert dup == {"deduped": True, "event_id": "evt-1", "alerts": []}


# ── US-3 AC1: adverse-action decision auto-starts the ECOA clock ────────────
def test_denied_stage_change_starts_disclosure_clock():
    service.record_activity(ActivityEvent(event_type="deal.stage_changed", entity_id="deal-9",
                                          outcome="DENIED", engine="underwriting"))
    timer = get_disclosure_repository().get("deal-9")
    assert timer is not None and timer.kind == "adverse_action"


# ── consent.updated events keep the consent store in sync ──────────────────
def test_consent_updated_event_applies_to_store():
    service.record_activity(ActivityEvent(
        event_type="consent.updated", entity_id="l3",
        payload={"lead_id": "l3", "channel": "email", "opted_in": False}))
    got = service.get_consent_repository().get("l3", "email")
    assert got is not None and got.opted_in is False


# ── Missing explainability now opens a case (§8), not just an alert ─────────
def test_missing_explainability_opens_a_case():
    service.record_activity(ActivityEvent(event_type="decision", actor_type="engine",
                                          engine="lead-scoring", entity_id="l4"))
    assert any(c.rule_id == "explainability_required" for c in get_case_repository().list())
