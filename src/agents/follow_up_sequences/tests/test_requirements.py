"""Acceptance / requirements test suite for the Follow-up Sequences Engine (FUS).

Exercises the FRs, business rules, NFRs, and acceptance criteria found in:
- docs/ticket_description_fus.md
- docs/implementation_status_fus.md

All tests are self-contained, in-memory, broker-free, and run without real
Twilio/SendGrid/Redis/Kafka infrastructure.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app import (
    guardrails,
    notifications,
    providers,
    quiet_hours,
    scheduler,
    service,
)
from app.clients import compliance_client
from app.core.config import get_settings
from app.domain.models import (
    Branch,
    Contact,
    SequenceDef,
    Step,
)
from app.frequency import FrequencyStore, RedisFrequencyStore, _day_key, get_store
from app.messaging import kafka_bus
from app.providers import ProviderResult

DAY = datetime(2026, 8, 4, 14, 0, tzinfo=UTC)  # 10:00 America/New_York — allowed
NIGHT = datetime(2026, 8, 4, 7, 0, tzinfo=UTC)  # 03:00 America/New_York — quiet hours


def _contact(cid: str, **kw) -> Contact:
    base = {
        "name": "Ana",
        "property_name": "12 Oak St",
        "agent_name": "Lee",
        "timezone": "America/New_York",
        "consent_basis": "express_written",
        "email": "a@x.com",
        "phone": "+15551234567",
    }
    return service.upsert_contact(Contact(contact_id=cid, **{**base, **kw}))


def _seq(
    sid: str, steps: list[Step] | None = None, stop_rules: list[str] | None = None
) -> SequenceDef:
    steps = steps or [Step(id="s1", channel="email", delay="PT0M", template="Hi {{lead_name}}")]
    return service.upsert_sequence(
        SequenceDef(
            sequence_id=sid,
            name=sid,
            version=1,
            steps=steps,
            stop_rules=stop_rules or ["replied", "converted", "opt_out"],
        )
    )


# ── Sequence builder (FR-001) ─────────────────────────────────────────────
def test_sequence_builder_with_steps_branches_and_exits(
    client: TestClient, api_headers: dict
) -> None:
    seq = {
        "sequence_id": "req-seq",
        "name": "Requirement Demo",
        "version": 1,
        "steps": [
            {
                "id": "s1",
                "channel": "email",
                "delay": "PT0M",
                "template": "Welcome {{lead_name}}",
                "branches": [
                    {"if": "opened", "goto": "s3"},
                    {"if": "no_open_after:PT48H", "goto": "s2"},
                ],
            },
            {"id": "s2", "channel": "sms", "delay": "PT0M", "template": "Nudge"},
            {"id": "s3", "channel": "email", "delay": "PT0M", "template": "Value"},
        ],
        "stop_rules": ["replied", "converted", "opt_out", "max_steps:7"],
    }
    r = client.post("/api/v1/sequences", json=seq, headers=api_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["sequence_id"] == "req-seq"
    assert data["version"] == 1
    assert data["status"] == "active"
    assert len(data["steps"]) == 3
    assert data["steps"][0]["branches"][0]["cond"] == "opened"
    assert data["steps"][0]["branches"][0]["goto"] == "s3"
    assert "max_steps:7" in data["stop_rules"]

    r2 = client.get("/api/v1/sequences/req-seq")
    assert r2.status_code == 200
    assert r2.json()["name"] == "Requirement Demo"


# ── Enrollment + de-duplication (FR-002) ──────────────────────────────────
def test_enrollment_and_deduplication(client: TestClient, api_headers: dict) -> None:
    client.post(
        "/api/v1/contacts",
        json={
            "contact_id": "c-enroll",
            "name": "Ana",
            "timezone": "America/New_York",
            "consent_basis": "express_written",
            "email": "a@x.com",
        },
        headers=api_headers,
    )
    client.post(
        "/api/v1/sequences",
        json={
            "sequence_id": "seq-enroll",
            "name": "Enroll Demo",
            "steps": [{"id": "s1", "channel": "email", "delay": "PT0M", "template": "Hi"}],
            "stop_rules": [],
        },
        headers=api_headers,
    )

    r1 = client.post(
        "/api/v1/enrollments",
        json={"contact_id": "c-enroll", "sequence_id": "seq-enroll"},
        headers=api_headers,
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "enrolled"
    eid = r1.json()["enrollment"]["enrollment_id"]

    r2 = client.post(
        "/api/v1/enrollments",
        json={"contact_id": "c-enroll", "sequence_id": "seq-enroll"},
        headers=api_headers,
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "duplicate"
    assert r2.json()["enrollment"]["enrollment_id"] == eid


# ── Quiet-hours / best-time scheduler (FR-003) ────────────────────────────
def test_quiet_hours_and_best_time_scheduler() -> None:
    """Scheduler clamps into the contact-local legal window and snaps to the
    Timing Optimizing Engine's best-time hour when available."""
    settings = SimpleNamespace(
        default_timezone="America/New_York",
        quiet_hours_start=21,
        quiet_hours_end=8,
    )

    # 10:00 NY is inside 08:00-21:00.
    assert quiet_hours.is_allowed(
        DAY,
        tz="America/New_York",
        default_tz="America/New_York",
        quiet_end=8,
        quiet_start=21,
    )
    # 03:00 NY is quiet hours.
    assert not quiet_hours.is_allowed(
        NIGHT,
        tz="America/New_York",
        default_tz="America/New_York",
        quiet_end=8,
        quiet_start=21,
    )

    contact = Contact(contact_id="c-sched", timezone="America/New_York")
    # NIGHT (03:00 NY, no best time) → next allowed window 08:00 NY = 12:00 UTC.
    nxt = scheduler.next_send_at(NIGHT, timedelta(0), contact, settings)
    assert nxt == datetime(2026, 8, 4, 12, 0, tzinfo=UTC)

    # With a best-time hour of 10, 03:00 NY → 10:00 NY = 14:00 UTC.
    contact_best = Contact(
        contact_id="c-sched-best", timezone="America/New_York", best_time_hour=10
    )
    nxt_best = scheduler.next_send_at(NIGHT, timedelta(0), contact_best, settings)
    assert nxt_best == datetime(2026, 8, 4, 14, 0, tzinfo=UTC)


# ── Consent/DNC/frequency-cap guardrails (FR-004 / FR-005) ────────────────
def test_guardrails_fail_closed_consent_dnc_and_frequency_cap() -> None:
    """Guardrail Gateway is fail-closed: no send without verified consent,
    to DNC contacts, or over the global frequency cap."""
    settings = SimpleNamespace(
        quiet_hours_start=21,
        quiet_hours_end=8,
        default_timezone="America/New_York",
        freq_cap_day=2,
        freq_cap_week=5,
        consent_cache_ttl_s=60,
        manual_outreach_cooldown_h=24,
    )
    freq = FrequencyStore()
    contact = Contact(
        contact_id="c-guard", timezone="America/New_York", consent_basis="express_written"
    )

    # All clear → send.
    d = guardrails.check(contact, sequence_id="seq", now=DAY, settings=settings, freq=freq)
    assert d.disposition == "send"
    assert d.allowed is True

    # Unknown/insufficient consent → hold (fail-closed, never send).
    c_unknown = contact.model_copy(update={"consent_basis": "unknown"})
    d = guardrails.check(c_unknown, sequence_id="seq", now=DAY, settings=settings, freq=freq)
    assert d.disposition == "hold"
    assert d.reason == "consent_unknown"

    # DNC → stop.
    c_dnc = contact.model_copy(update={"dnc": True})
    d = guardrails.check(c_dnc, sequence_id="seq", now=DAY, settings=settings, freq=freq)
    assert d.disposition == "stop"
    assert d.reason == "dnc"

    # Global frequency cap exhausted → hold.
    for _ in range(settings.freq_cap_day):
        freq.increment("default", "c-guard", "seq", DAY)
    d = guardrails.check(contact, sequence_id="seq", now=DAY, settings=settings, freq=freq)
    assert d.disposition == "hold"
    assert d.reason == "frequency_cap"


# ── Redis-backed frequency counters (FR-005) ──────────────────────────────
class _FakeRedisPipeline:
    def __init__(self, client: _FakeRedis) -> None:
        self.client = client
        self._ops: list[tuple[str, ...]] = []

    def incr(self, key: str) -> _FakeRedisPipeline:
        self._ops.append(("incr", key))
        return self

    def expire(self, key: str, ttl: int) -> _FakeRedisPipeline:
        self._ops.append(("expire", key, ttl))
        return self

    def execute(self) -> list:
        results = []
        for op in self._ops:
            if op[0] == "incr":
                results.append(self.client.incr(op[1]))
            elif op[0] == "expire":
                results.append(self.client.expire(op[1], op[2]))
        return results


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, int] = {}
        self._ttl: dict[str, int] = {}

    def get(self, key: str):
        return self._store.get(key)

    def set(self, key: str, value: int) -> bool:
        self._store[key] = value
        return True

    def incr(self, key: str) -> int:
        self._store[key] = self._store.get(key, 0) + 1
        return self._store[key]

    def expire(self, key: str, ttl: int) -> bool:
        self._ttl[key] = ttl
        return True

    def ping(self) -> bool:
        return True

    def pipeline(self) -> _FakeRedisPipeline:
        return _FakeRedisPipeline(self)

    def clear(self) -> None:
        self._store.clear()
        self._ttl.clear()


def test_redis_backed_frequency_counters_when_cache_gateway_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When CACHE_GATEWAY=redis the engine uses a Redis-backed FrequencyStore
    and increments both global and per-sequence day counters on a successful send."""
    monkeypatch.setenv("CACHE_GATEWAY", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379/0")
    get_settings.cache_clear()

    fake = _FakeRedis()
    monkeypatch.setattr("app.frequency._redis_client", lambda: fake)

    _contact("c-redis")
    _seq(
        "seq-redis",
        steps=[Step(id="s1", channel="email", delay="PT0M", template="Hi {{lead_name}}")],
        stop_rules=["max_steps:1"],
    )

    store = get_store()
    assert isinstance(store, RedisFrequencyStore)

    r = service.enroll("c-redis", "seq-redis", now=DAY)
    eid = r["enrollment"]["enrollment_id"]
    service.tick(now=DAY)

    # Contact was sent to, so global and per-sequence day counters are 1.
    global_day = _day_key("default", "c-redis", "__global__", DAY)
    seq_day = _day_key("default", "c-redis", "seq-redis", DAY)
    assert fake.get(global_day) == 1
    assert fake.get(seq_day) == 1

    e = service.get_enrollment(eid)
    assert e["state"] == "completed"  # max_steps:1 completes after the single send


# ── Engagement-driven branching (FR-007) ──────────────────────────────────
def test_engagement_driven_branching(client: TestClient, api_headers: dict) -> None:
    """Opened → Path A; not-opened after the timed window → Path B."""
    _contact("c-branch")
    _seq(
        "seq-branch",
        steps=[
            Step(
                id="s1",
                channel="email",
                delay="PT0M",
                template="intro",
                branches=[
                    Branch(cond="opened", goto="s3"),
                    Branch(cond="no_open_after:PT2S", goto="s2"),
                ],
            ),
            Step(id="s2", channel="sms", delay="PT0M", template="path B nudge"),
            Step(id="s3", channel="email", delay="PT0M", template="path A value"),
        ],
        stop_rules=[],
    )

    r = service.enroll("c-branch", "seq-branch", now=DAY)
    eid = r["enrollment"]["enrollment_id"]

    service.tick(now=DAY)
    assert [s["channel"] for s in providers.sent_log] == ["email"]

    # Path A: open the email, branch to s3, and send it.
    client.post(
        "/api/v1/webhooks/engagement",
        json={"contact_id": "c-branch", "enrollment_id": eid, "step_id": "s1", "kind": "open"},
        headers=api_headers,
    )
    service.tick(now=DAY)
    service.tick(now=DAY)
    assert [s["channel"] for s in providers.sent_log] == ["email", "email"]
    assert service.get_enrollment(eid)["current_step_id"] == "s3"


# ── Stop conditions (FR-009) ──────────────────────────────────────────────
def test_stop_condition_reply_pauses(client: TestClient, api_headers: dict) -> None:
    _contact("c-reply")
    _seq("seq-reply", steps=[Step(id="s1", channel="email", delay="PT0M", template="Hi")])
    r = service.enroll("c-reply", "seq-reply", now=DAY)
    eid = r["enrollment"]["enrollment_id"]
    service.tick(now=DAY)

    client.post(
        "/api/v1/webhooks/engagement",
        json={"contact_id": "c-reply", "enrollment_id": eid, "step_id": "s1", "kind": "reply"},
        headers=api_headers,
    )
    e = service.get_enrollment(eid)
    assert e["state"] == "paused"
    assert e["stopped_reason"] == "replied"


def test_stop_condition_apply_converted_stops(client: TestClient, api_headers: dict) -> None:
    _contact("c-apply")
    _seq("seq-apply", steps=[Step(id="s1", channel="email", delay="PT0M", template="Hi")])
    r = service.enroll("c-apply", "seq-apply", now=DAY)
    eid = r["enrollment"]["enrollment_id"]

    resp = client.post(
        "/api/v1/stage-changed",
        json={"contact_id": "c-apply", "stage": "application"},
        headers=api_headers,
    )
    assert resp.json()["stopped"] == 1
    e = service.get_enrollment(eid)
    assert e["state"] == "stopped"
    assert e["stopped_reason"] == "converted"


def test_stop_condition_opt_out_permanently_blocks_reenroll(
    client: TestClient, api_headers: dict
) -> None:
    _contact("c-optout")
    _seq("seq-optout")
    service.enroll("c-optout", "seq-optout", now=DAY)

    resp = client.post("/api/v1/contacts/c-optout/opt-out", headers=api_headers)
    assert resp.json()["opted_out"] is True

    resp2 = client.post(
        "/api/v1/enrollments",
        json={"contact_id": "c-optout", "sequence_id": "seq-optout"},
        headers=api_headers,
    )
    assert resp2.json()["status"] == "blocked"


def test_stop_condition_max_steps_completes() -> None:
    _contact("c-max")
    _seq(
        "seq-max",
        steps=[
            Step(id="s1", channel="email", delay="PT0M", template="one"),
            Step(id="s2", channel="email", delay="PT0M", template="two"),
            Step(id="s3", channel="email", delay="PT0M", template="three"),
        ],
        stop_rules=["max_steps:2"],
    )
    r = service.enroll("c-max", "seq-max", now=DAY)
    eid = r["enrollment"]["enrollment_id"]

    # Each tick advances one phase (send -> branch -> send). 5 ticks is plenty
    # to send s1, branch, send s2, and trigger the max_steps:2 completion.
    for _ in range(5):
        service.tick(now=DAY)

    e = service.get_enrollment(eid)
    assert e["state"] == "completed"
    assert e["stopped_reason"] == "max_steps"


# ── CMS precheck + cross-agent header propagation ─────────────────────────
def test_cms_precheck_http_client_and_header_propagation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPLIANCE_MONITOR_URL", "http://mock-cms:8000")
    monkeypatch.setenv("FUS_PRECHECK_ENABLED", "true")
    monkeypatch.setenv("COMPLIANCE_API_KEY", "cms-key")
    get_settings.cache_clear()

    captured: dict = {}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ARG002
            pass

        def __enter__(self):
            mock = MagicMock()
            response = MagicMock()
            response.status_code = 200
            response.is_success = True
            response.json.return_value = {"status": "allowed"}
            response.text = str({"status": "allowed"})
            response.raise_for_status.return_value = None

            def _capture_post(url, *, json, headers, **kwargs):  # noqa: ARG001
                captured["url"] = url
                captured["json"] = json
                captured["headers"] = headers
                return response

            mock.post = _capture_post
            return mock

        def __exit__(self, *args, **kwargs) -> bool:  # noqa: ARG002
            return False

    monkeypatch.setattr(httpx, "Client", FakeClient)

    contact = Contact(contact_id="c-cms", consent_basis="express_written")
    result = compliance_client.precheck(contact, "email", "hello", DAY, settings=get_settings())

    assert result == {"status": "allowed"}
    assert captured["url"] == "http://mock-cms:8000/api/v1/precheck"
    assert captured["json"]["contact_id"] == "c-cms"
    assert captured["json"]["channel"] == "email"
    assert captured["headers"]["X-Request-ID"]
    assert captured["headers"]["X-API-Key"] == "cms-key"


# ── Real channel providers: mock fallback + retry (FR-006 / FR-019) ───────
def test_channel_providers_mock_fallback_and_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    providers.reset()
    contact = Contact(contact_id="c-prov", phone="+15551234567", email="a@x.com")

    # No credentials configured → falls back to in-process mock provider.
    result = providers.deliver(contact, "sms", "hello", max_attempts=2)
    assert result.success is True
    assert result.provider == "mock-sms"
    assert any(s["channel"] == "sms" for s in providers.sent_log)

    # Provider raises rate-limit; deliver retries with exponential backoff.
    calls: list[object] = []

    def flaky_provider(c: Contact, channel: str, body: str) -> ProviderResult:
        calls.append(1)
        if len(calls) <= 2:
            raise providers.ProviderRateLimit("rate limited")
        return ProviderResult(True, "mock-retry")

    providers.set_provider(flaky_provider)
    sleeps: list[float] = []
    monkeypatch.setattr(providers.time, "sleep", sleeps.append)

    result = providers.deliver(contact, "email", "retry me", max_attempts=4)
    assert result.success is True
    assert result.attempts == 3
    assert result.provider == "mock-retry"
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]

    # With real Twilio credentials set, deliver calls the Twilio HTTP endpoint.
    providers.reset()
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+1999")
    get_settings.cache_clear()

    captured: dict = {}

    class TwilioFakeClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ARG002
            pass

        def __enter__(self):
            mock = MagicMock()

            def _capture_post(url, *, auth, data, **kwargs):  # noqa: ARG001
                captured["url"] = url
                captured["auth"] = auth
                captured["data"] = data
                return MagicMock(status_code=202, is_success=True, text="ok")

            mock.post = _capture_post
            return mock

        def __exit__(self, *args, **kwargs) -> bool:  # noqa: ARG002
            return False

    monkeypatch.setattr(httpx, "Client", TwilioFakeClient)

    result = providers.deliver(contact, "sms", "hi", max_attempts=1)
    assert result.success is True
    assert result.provider == "twilio"
    assert "twilio.com" in captured["url"]
    assert captured["data"]["From"] == "+1999"
    assert captured["data"]["To"] == "+15551234567"


# ── Kafka producer/consumer contract + in-memory fallback (FR-014) ────────
def test_kafka_producer_contract_and_in_memory_fallback() -> None:
    kafka_bus.reset_kafka_bus()
    row = kafka_bus.produce_sync("outreach.sent", {"contact_id": "c1"})
    assert row is not None
    assert row["topic"] == "crm.outreach.sent"
    assert row["key"] == "c1"
    assert row["transport"] == "in_process_bus"

    stats = kafka_bus.bus_stats()
    assert stats["messages_total"] == 1
    assert stats["in_memory_lag"] == 1
    assert stats["topics"]["crm.outreach.sent"] == 1


def test_kafka_partition_key_precedence() -> None:
    assert kafka_bus.partition_key({"deal_id": "d1", "lead_id": "l1"}) == "d1"
    assert kafka_bus.partition_key({"lead_id": "l1", "contact_id": "c1"}) == "l1"
    assert kafka_bus.partition_key({"contact_id": "c1", "entity_id": "e1"}) == "c1"
    assert kafka_bus.partition_key({"entity_id": "e1", "id": "i1"}) == "e1"
    assert kafka_bus.partition_key({"id": "i1"}) == "i1"
    assert kafka_bus.partition_key({}) == "unknown"


def test_kafka_consumer_in_memory_fallback_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Consumer falls back to the in-memory bus and dispatches deal.stage_changed
    events to the service; poison messages do not stop the consumer."""
    try:
        import aiokafka

        class _FakeAIOKafkaConsumer:
            def __init__(self, *args, **kwargs) -> None:  # noqa: ARG002
                pass

            async def start(self) -> None:
                raise OSError("no broker")

            async def stop(self) -> None:
                pass

        monkeypatch.setattr(aiokafka, "AIOKafkaConsumer", _FakeAIOKafkaConsumer)
    except ModuleNotFoundError:
        pass

    monkeypatch.setenv("KAFKA_CONSUMER_ENABLED", "true")
    get_settings.cache_clear()
    kafka_bus.reset_kafka_bus()

    mock_record = MagicMock(side_effect=[RuntimeError("poison"), {"stopped": 1}])
    monkeypatch.setattr("app.service.record_stage_change", mock_record)

    async def run() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(kafka_bus.consume_loop(stop))
        await asyncio.sleep(0.01)

        # Poison message followed by a valid message.
        kafka_bus.produce_sync("deal.stage_changed", {"contact_id": "c1", "stage": "applied"})
        kafka_bus.produce_sync("deal.stage_changed", {"contact_id": "c2", "stage": "funded"})

        for _ in range(100):
            if mock_record.call_count >= 2:
                break
            await asyncio.sleep(0.01)

        stop.set()
        await task

    asyncio.run(run())

    assert mock_record.call_count == 2
    assert kafka_bus.bus_stats()["in_memory_lag"] == 0


# ── Bounce/complaint suppression (FR-015) ─────────────────────────────────
def test_suppression_webhook_bounce_stops_sequence(client: TestClient, api_headers: dict) -> None:
    _contact("c-sup")
    _seq(
        "seq-sup",
        steps=[Step(id="s1", channel="email", delay="PT0M", template="Hi")],
        stop_rules=[],
    )
    r = service.enroll("c-sup", "seq-sup", now=DAY)
    eid = r["enrollment"]["enrollment_id"]
    service.tick(now=DAY)

    resp = client.post(
        "/api/v1/webhooks/suppression",
        json={"contact_id": "c-sup", "kind": "bounce", "channel": "email", "provider": "sendgrid"},
        headers=api_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["suppressed"] is True
    assert data["stopped"] == 1

    e = service.get_enrollment(eid)
    assert e["state"] == "stopped"
    assert e["stopped_reason"].startswith("suppressed")

    c = service.get_contact("c-sup")
    assert c["suppressed"] is True


def test_suppression_complaint_sets_opt_out_and_dnc(client: TestClient, api_headers: dict) -> None:
    _contact("c-sup2")
    resp = client.post(
        "/api/v1/webhooks/suppression",
        json={
            "contact_id": "c-sup2",
            "kind": "complaint",
            "channel": "email",
            "provider": "sendgrid",
        },
        headers=api_headers,
    )
    assert resp.status_code == 200

    c = service.get_contact("c-sup2")
    assert c["suppressed"] is True
    assert c["opted_out"] is True
    assert c["dnc"] is True


# ── LO/owner reply notifications (FR-016) ─────────────────────────────────
def test_owner_reply_notification(client: TestClient, api_headers: dict) -> None:
    notifications.reset()
    _contact("c-owner", owner_id="lo:amy")
    _seq(
        "seq-owner",
        steps=[Step(id="s1", channel="email", delay="PT0M", template="Hi {{lead_name}}")],
        stop_rules=[],
    )
    r = service.enroll("c-owner", "seq-owner", now=DAY)
    eid = r["enrollment"]["enrollment_id"]
    service.tick(now=DAY)

    client.post(
        "/api/v1/webhooks/engagement",
        json={
            "contact_id": "c-owner",
            "enrollment_id": eid,
            "step_id": "s1",
            "kind": "reply",
            "channel": "email",
            "payload": {"body": "I want to apply"},
        },
        headers=api_headers,
    )

    e = service.get_enrollment(eid)
    assert e["state"] == "paused"
    assert e["stopped_reason"] == "replied"

    log = notifications.notification_log()
    assert len(log) >= 1
    assert log[-1]["owner_id"] == "lo:amy"
    assert log[-1]["contact_id"] == "c-owner"
    assert "replied" in log[-1]["message"]


# ── Default-dev-key and CORS wildcard rejection (NFR / security) ──────────
def test_default_dev_key_and_cors_wildcard_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("API_KEY", "change-me-dev-key")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="Default dev API key is not allowed"):
        get_settings()

    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("API_KEY", "real-staging-key")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="Wildcard CORS origin"):
        get_settings()
