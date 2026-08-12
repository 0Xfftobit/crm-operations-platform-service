"""CMS acceptance / requirements test suite.

Exercises the Compliance Monitor System ticket requirements, business rules, NFRs and
acceptance criteria from ``docs/ticket_description_cms.md`` and
``docs/implementation_status_cms.md``.  Every test is self-contained, in-memory and
broker-free so the suite stays fast and hermetic.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import audit, kafka_bus
from app.core.config import Settings, get_settings
from app.main import create_app

# conftest does not define a BASE constant; CMS routers are mounted under /api/v1.
BASE = "/api/v1"


def _headers(settings: Settings | None = None) -> dict:
    return {"X-API-Key": (settings or get_settings()).api_key}


# ── FR-002/003/004/017: Pre-send compliance hook ───────────────────────────


def test_precheck_fail_closed_on_missing_consent(client):
    resp = client.post(
        f"{BASE}/precheck",
        headers=_headers(),
        json={"channel": "sms", "contact_id": "unknown_lead"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "deny"
    assert "no_consent" in body["reasons"]
    assert body["audit_seq"] is not None


def test_precheck_blocks_opted_out_contact(client):
    client.put(
        f"{BASE}/consent",
        headers=_headers(),
        json={"lead_id": "l1", "channel": "sms", "opted_in": False},
    )
    resp = client.post(
        f"{BASE}/precheck",
        headers=_headers(),
        json={"channel": "sms", "contact_id": "l1"},
    )
    body = resp.json()
    assert body["decision"] == "deny"
    assert "opt_out" in body["reasons"]


def test_precheck_blocks_dnd_registry_for_callable_channels(client):
    client.put(
        f"{BASE}/consent",
        headers=_headers(),
        json={
            "lead_id": "l2",
            "channel": "sms",
            "opted_in": True,
            "on_dnd_registry": True,
        },
    )
    resp = client.post(
        f"{BASE}/precheck",
        headers=_headers(),
        json={"channel": "sms", "contact_id": "l2"},
    )
    body = resp.json()
    assert body["decision"] == "deny"
    assert "dnd_registry" in body["reasons"]


def test_precheck_blocks_quiet_hours_in_contact_timezone(client):
    client.put(
        f"{BASE}/consent",
        headers=_headers(),
        json={
            "lead_id": "l3",
            "channel": "sms",
            "opted_in": True,
            "timezone": "America/New_York",
        },
    )
    # 03:00 UTC == 22:00 EST, inside the 21:00-08:00 quiet window.
    when = datetime(2026, 1, 15, 3, 0, tzinfo=UTC).isoformat()
    resp = client.post(
        f"{BASE}/precheck",
        headers=_headers(),
        json={"channel": "sms", "contact_id": "l3", "scheduled_for": when},
    )
    body = resp.json()
    assert body["decision"] == "deny"
    assert "quiet_hours_violation" in body["reasons"]


def test_precheck_can_spam_requires_unsubscribe_and_sender_identity(client):
    client.put(
        f"{BASE}/consent",
        headers=_headers(),
        json={"lead_id": "l4", "channel": "email", "opted_in": True},
    )
    deny = client.post(
        f"{BASE}/precheck",
        headers=_headers(),
        json={
            "channel": "email",
            "contact_id": "l4",
            "has_unsubscribe": False,
            "sender_id": "",
        },
    )
    assert deny.json()["decision"] == "deny"
    assert "missing_unsubscribe" in deny.json()["reasons"]
    assert "missing_sender_identity" in deny.json()["reasons"]

    allow = client.post(
        f"{BASE}/precheck",
        headers=_headers(),
        json={
            "channel": "email",
            "contact_id": "l4",
            "has_unsubscribe": True,
            "sender_id": "CR Equity <noreply@crequity.ai>",
        },
    )
    assert allow.json()["decision"] == "allow"
    assert allow.json()["reasons"] == []


def test_precheck_blocks_high_risk_pii_in_outbound_content(client):
    client.put(
        f"{BASE}/consent",
        headers=_headers(),
        json={"lead_id": "l5", "channel": "sms", "opted_in": True},
    )
    resp = client.post(
        f"{BASE}/precheck",
        headers=_headers(),
        json={
            "channel": "sms",
            "contact_id": "l5",
            "content": "Your SSN is 123-45-6789",
        },
    )
    body = resp.json()
    assert body["decision"] == "deny"
    assert any("pii_exposure" in r and "ssn" in r for r in body["reasons"])


# ── FR-003: Consent management + Redis-backed hot cache ────────────────────


def test_consent_tracked_per_lead_per_channel_and_respected(client):
    client.put(
        f"{BASE}/consent",
        headers=_headers(),
        json={"lead_id": "l6", "channel": "sms", "opted_in": True},
    )
    client.put(
        f"{BASE}/consent",
        headers=_headers(),
        json={"lead_id": "l6", "channel": "email", "opted_in": False},
    )
    resp = client.get(f"{BASE}/consent/l6", headers=_headers())
    assert resp.status_code == 200
    by_channel = {c["channel"]: c["opted_in"] for c in resp.json()["consents"]}
    assert by_channel == {"sms": True, "email": False}

    assert (
        client.post(
            f"{BASE}/precheck",
            headers=_headers(),
            json={"channel": "sms", "contact_id": "l6"},
        ).json()["decision"]
        == "allow"
    )
    assert (
        client.post(
            f"{BASE}/precheck",
            headers=_headers(),
            json={"channel": "email", "contact_id": "l6"},
        ).json()["decision"]
        == "deny"
    )


def test_redis_backed_consent_cache_and_precheck_integration(monkeypatch):
    """CACHE_GATEWAY=redis persists consent in Redis and the pre-send hook honors it."""
    fakeredis = pytest.importorskip("fakeredis")
    redis = pytest.importorskip("redis")

    monkeypatch.setenv("CACHE_GATEWAY", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://fake")
    get_settings.cache_clear()

    from app import cache

    cache.reset()
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis.Redis, "from_url", lambda *args, **kwargs: fake)

    settings = get_settings()
    with TestClient(create_app()) as c:
        c.put(
            f"{BASE}/consent",
            headers=_headers(settings),
            json={
                "lead_id": "lead_redis",
                "channel": "sms",
                "opted_in": False,
                "on_dnd_registry": False,
            },
        )
        raw = fake.get("consent:lead_redis:sms")
        assert raw is not None
        assert '"opted_in": false' in raw

        resp = c.post(
            f"{BASE}/precheck",
            headers=_headers(settings),
            json={"channel": "sms", "contact_id": "lead_redis"},
        )
        assert resp.json()["decision"] == "deny"
        assert any("opt_out" in r for r in resp.json()["reasons"])


# ── FR-008/CEAAPSS-566: Immutable, hash-chained audit ledger ───────────────


def test_activity_appends_a_verified_audit_entry(client):
    resp = client.post(
        f"{BASE}/activity",
        headers=_headers(),
        json={
            "event_type": "decision",
            "actor_type": "engine",
            "engine": "lead-scoring",
            "entity_id": "lead_audit",
            "action": "score",
            "outcome": "A",
            "explain_ref": "expl-1",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "audit_seq" in body

    verify = client.get(f"{BASE}/audit/verify")
    assert verify.json()["verified"] is True

    search = client.get(
        f"{BASE}/audit/search?entity_id=lead_audit", headers=_headers()
    )
    assert search.json()["count"] == 1

    export = client.get(
        f"{BASE}/audit/export?fmt=json",
        headers={"X-Role": "auditor"},
    )
    assert export.status_code == 200
    assert export.json()["chain_verified"] is True


def test_audit_chain_detects_tampering(client):
    client.post(
        f"{BASE}/activity",
        headers=_headers(),
        json={"event_type": "comm_sent", "entity_id": "lead_1"},
    )
    client.post(
        f"{BASE}/activity",
        headers=_headers(),
        json={"event_type": "comm_sent", "entity_id": "lead_2"},
    )
    # Tamper with the ledger outside the append-only API.
    audit.all_entries()[0].outcome = "tampered"
    verify = client.get(f"{BASE}/audit/verify")
    assert verify.json()["verified"] is False
    assert verify.json()["broken_at"] == 1


# ── FR-005/US-2: Fair-lending 4/5ths rule ─────────────────────────────────


def test_fairness_four_fifths_rule_opens_critical_case(client):
    resp = client.post(
        f"{BASE}/fairness/evaluate",
        headers=_headers(),
        json={
            "sub_agent": "lead_assignment",
            "cohort_dimension": "zip",
            "cohorts": {
                "a": {"selected": 42, "total": 100},
                "b": {"selected": 31, "total": 100},
            },
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["passes"] is False
    assert body["min_sample_met"] is True
    assert body["ratio"] < get_settings().fairness_ratio_min

    cases = client.get(f"{BASE}/cases", headers=_headers()).json()["cases"]
    assert any(
        c["rule_id"] == "four_fifths_rule" and c["severity"] == "critical"
        for c in cases
    )


# ── FR-006/BR-5: ECOA / adverse-action disclosure clocks ───────────────────


def test_ecoa_adverse_action_disclosure_clock_lifecycle(client):
    resp = client.post(
        f"{BASE}/disclosures/stage",
        headers=_headers(),
        json={"deal_id": "deal-1", "stage": "denied"},
    )
    assert resp.status_code == 200
    assert resp.json()["started"]

    disclosures = client.get(f"{BASE}/disclosures", headers=_headers()).json()[
        "disclosures"
    ]
    adverse = next((d for d in disclosures if d["kind"] == "adverse_action"), None)
    assert adverse is not None
    due = datetime.fromisoformat(adverse["due_at"])
    started = datetime.fromisoformat(adverse["started_at"])
    assert (due - started).days == get_settings().adverse_action_days == 30

    satisfy = client.post(
        f"{BASE}/disclosures/deal-1/satisfy",
        headers=_headers(),
    )
    assert satisfy.status_code == 200
    assert satisfy.json()["satisfied"] is True


# ── FR-007: PII scan / data privacy ────────────────────────────────────────


def test_pii_scan_in_activity_raises_data_privacy_alert(client):
    resp = client.post(
        f"{BASE}/activity",
        headers=_headers(),
        json={
            "event_type": "comm_sent",
            "engine": "crm",
            "entity_id": "lead_pii",
            "reasoning": "Credit card 4111-1111-1111-1111 was exposed",
        },
    )
    assert resp.status_code == 200
    alerts = client.get(f"{BASE}/alerts", headers=_headers()).json()["alerts"]
    assert any(
        a["domain"] == "data_privacy" and "credit_card" in a["message"]
        for a in alerts
    )


def test_right_to_erasure_anonymizes_lead_and_blocks_comms(client):
    client.put(
        f"{BASE}/consent",
        headers=_headers(),
        json={"lead_id": "l7", "channel": "sms", "opted_in": True},
    )
    resp = client.post(
        f"{BASE}/privacy/erase",
        headers=_headers(),
        json={"lead_id": "l7", "reason": "gdpr_request"},
    )
    assert resp.status_code == 200
    assert resp.json()["anonymized"] is True

    # Consent removed / anonymized => no consent => fail-closed deny.
    pre = client.post(
        f"{BASE}/precheck",
        headers=_headers(),
        json={"channel": "sms", "contact_id": "l7"},
    )
    assert pre.json()["decision"] == "deny"


# ── FR-010/011/012: Case / alert management ────────────────────────────────


def test_case_lifecycle_create_and_patch(client):
    created = client.post(
        f"{BASE}/cases",
        headers=_headers(),
        json={
            "rule_id": "manual_review",
            "domain": "regulatory",
            "severity": "warning",
            "entity_id": "lead_case",
            "message": "Suspicious activity flagged",
        },
    )
    assert created.status_code == 200
    case_id = created.json()["case_id"]

    patch = client.patch(
        f"{BASE}/cases/{case_id}",
        headers=_headers(),
        json={"status": "investigating", "assignee": "compliance-1"},
    )
    assert patch.status_code == 200
    assert patch.json()["status"] == "investigating"
    assert patch.json()["assignee"] == "compliance-1"

    # Case update should have published an event for Engine 8 correlation.
    from app import events

    assert any(e["type"] == "compliance.case.updated" for e in events.get_events())


# ── CEAAPSS-565/BR-6: Rule versioning + two-step approval + staging ────────


def test_rule_versioning_two_step_approval_and_publish(client):
    proposed = client.post(
        f"{BASE}/rules/changes",
        headers=_headers(),
        json={
            "rule_id": "tcpa_quiet_hours",
            "domain": "marketing",
            "definition": {"start": "20:00"},
            "change_reason": "Tighten quiet hours",
            "changed_by": "alice",
        },
    )
    assert proposed.status_code == 200
    change_id = proposed.json()["change_id"]
    assert proposed.json()["status"] == "proposed"

    # Proposer cannot approve their own change.
    self_approve = client.post(
        f"{BASE}/rules/changes/{change_id}/approve",
        headers=_headers(),
        json={"approved_by": "alice"},
    )
    assert self_approve.status_code == 409

    # Separate approver + deploy publishes to production.
    approved = client.post(
        f"{BASE}/rules/changes/{change_id}/approve",
        headers=_headers(),
        json={"approved_by": "bob"},
    )
    assert approved.status_code == 200

    deployed = client.post(
        f"{BASE}/rules/changes/{change_id}/deploy",
        headers=_headers(),
    )
    assert deployed.status_code == 200
    prod = deployed.json()["production"]
    assert prod["stage"] == "production"
    assert prod["version"] == 1

    history = client.get(
        f"{BASE}/rules/tcpa_quiet_hours/history",
        headers=_headers(),
    )
    assert history.json()["changes"]
    assert any(c["status"] == "deployed" for c in history.json()["changes"])


# ── FR-013: Kafka producer/consumer contract + graceful in-memory fallback ─


def test_kafka_canonical_topics_partition_keys_and_memory_fallback():
    assert kafka_bus.canonical_topic("alert") == "compliance.alert"
    assert (
        kafka_bus.canonical_topic("deal.stage_changed", domain="crm")
        == "crm.deal.stage_changed"
    )
    assert kafka_bus.short_topic("compliance.case.opened") == "case.opened"
    assert kafka_bus.partition_key({"deal_id": "d1", "lead_id": "l1"}) == "d1"
    assert kafka_bus.partition_key({"foo": "bar"}) == "unknown"

    kafka_bus.reset_kafka_bus()
    before = kafka_bus.bus_stats()["total"]
    kafka_bus.produce_sync("compliance.alert", {"deal_id": "d1"})
    stats = kafka_bus.bus_stats()
    assert stats["total"] == before + 1
    assert "compliance.alert" in stats["topics"]
    assert stats["transport"]["memory"] == before + 1


def test_kafka_consumer_falls_back_to_in_memory_dispatch(monkeypatch):
    aiokafka = pytest.importorskip("aiokafka")
    monkeypatch.setenv("KAFKA_CONSUMER_ENABLED", "true")
    get_settings.cache_clear()
    kafka_bus.reset_kafka_bus()

    class _BoomConsumer:
        def __init__(self, **_) -> None:
            pass

        def subscribe(self, *, pattern=None, topics=None):
            pass

        async def start(self):
            raise OSError("no broker")

        async def stop(self):
            pass

    monkeypatch.setattr(aiokafka, "AIOKafkaConsumer", _BoomConsumer)

    async def _run():
        start = await kafka_bus.start_consumer()
        assert start["started"] is True

        before = len(audit.all_entries())
        record = await kafka_bus.produce(
            "crm.deal.stage_changed",
            {
                "event_type": "deal.stage_changed",
                "engine": "tas",
                "entity_id": "d1",
                "entity_type": "deal",
                "action": "stage_changed",
                "outcome": "denied",
                "source_seq": 1,
            },
        )
        assert record is not None

        # Drain the in-memory consumer.
        for _ in range(50):
            if kafka_bus.bus_stats()["in_memory_lag"] == 0:
                break
            await asyncio.sleep(0.01)

        # deal.stage_changed ingests the activity and starts an ECOA disclosure clock.
        assert len(audit.all_entries()) == before + 2
        await kafka_bus.stop_consumer()

    asyncio.run(_run())


# ── Security hardening: default dev key + wildcard CORS rejection ──────────


def test_default_dev_api_key_rejected_in_non_local_envs(monkeypatch):
    for env in ("production", "staging"):
        monkeypatch.setenv("ENV", env)
        monkeypatch.setenv("API_KEY", "change-me-dev-key")
        get_settings.cache_clear()
        with pytest.raises(ValueError, match="Default dev API key"):
            Settings(_env_file=None)


def test_wildcard_cors_rejected_in_non_local_envs(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("API_KEY", "prod-api-key")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="Wildcard CORS"):
        create_app()


def test_local_and_test_envs_allow_default_key_and_wildcard_cors(monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("API_KEY", "change-me-dev-key")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        assert c.get("/health").status_code == 200


# ── Request-id propagation on responses ───────────────────────────────────


def test_x_request_id_is_propagated_on_responses(client):
    resp = client.get(f"{BASE}/dashboard", headers=_headers())
    assert resp.status_code == 200
    assert "x-request-id" in resp.headers
    assert len(resp.headers["x-request-id"]) >= 16

    rid = f"req-{uuid4().hex}"
    resp2 = client.get(
        f"{BASE}/dashboard",
        headers={**_headers(), "x-request-id": rid},
    )
    assert resp2.headers["x-request-id"] == rid


# ── FR-020: RAG dashboard surfaces compliance posture ──────────────────────


def test_dashboard_reflects_fairness_breach(client):
    client.post(
        f"{BASE}/fairness/evaluate",
        headers=_headers(),
        json={
            "sub_agent": "lead_assignment",
            "cohort_dimension": "tract",
            "cohorts": {
                "a": {"selected": 42, "total": 100},
                "b": {"selected": 31, "total": 100},
            },
        },
    )
    dash = client.get(f"{BASE}/dashboard", headers=_headers()).json()
    assert dash["rag"]["ai_fairness"] in ("red", "amber")
    assert dash["overall"] in ("red", "amber")
    assert dash["audit_chain_verified"] is True
