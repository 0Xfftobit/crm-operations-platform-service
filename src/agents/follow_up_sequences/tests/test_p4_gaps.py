"""P4 hardening gaps: suppression, owner reply notifications, A/B allocation, CMS precheck."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import pytest

from app import abtest, notifications, providers, service
from app.clients import compliance_client
from app.core.config import get_settings
from app.domain.models import Contact, SequenceDef, Step, SuppressionEvent

DAY = datetime(2026, 8, 4, 14, 0, tzinfo=UTC)


def _contact(cid="c1", **kw):
    base = {"name": "Ana", "property_name": "12 Oak", "agent_name": "Lee",
            "timezone": "America/New_York", "consent_basis": "express_written",
            "email": "a@x.com", "phone": "+15551234567"}
    return service.upsert_contact(Contact(contact_id=cid, **{**base, **kw}))


def _seq(sid="seq", steps=None):
    steps = steps or [Step(id="s1", channel="email", delay="PT0M", template="Hi {{lead_name}}")]
    return service.upsert_sequence(SequenceDef(
        sequence_id=sid, name=sid, version=1, steps=steps,
        stop_rules=["replied", "converted", "opt_out"]))


# ── Suppression webhook + apply_suppression ─────────────────────────────────
@pytest.mark.parametrize("kind, expected_dnc, expected_opted_out", [
    ("bounce", False, False),
    ("complaint", True, True),
    ("spam", True, False),
    ("block", True, False),
])
def test_apply_suppression_flags_by_kind(kind, expected_dnc, expected_opted_out):
    _contact()
    evt = SuppressionEvent(contact_id="c1", kind=kind, channel="email", provider="sendgrid",
                           reason="5.1.1")
    result = service.apply_suppression(evt)

    assert result["suppressed"] is True
    assert result["contact_id"] == "c1"
    assert result["kind"] == kind

    c = service.get_contact("c1")
    assert c["suppressed"] is True
    assert c["dnc"] == expected_dnc
    assert c["opted_out"] == expected_opted_out
    assert c["suppressed_reason"].startswith(kind)


def test_apply_suppression_stops_active_enrollment():
    _contact()
    _seq()
    r = service.enroll("c1", "seq", now=DAY)
    eid = r["enrollment"]["enrollment_id"]

    evt = SuppressionEvent(contact_id="c1", kind="bounce", channel="email", provider="sendgrid")
    service.apply_suppression(evt)

    e = service.get_enrollment(eid)
    assert e["state"] == "stopped"
    assert e["stopped_reason"].startswith("suppressed")


def test_apply_suppression_unknown_contact():
    evt = SuppressionEvent(contact_id="unknown", kind="bounce", channel="email")
    result = service.apply_suppression(evt)
    assert result["status"] == "error"


def test_suppression_webhook_endpoint(client, api_headers):
    _contact()
    _seq()
    service.enroll("c1", "seq", now=DAY)

    resp = client.post(
        "/api/v1/webhooks/suppression",
        json={"contact_id": "c1", "kind": "bounce", "channel": "email", "provider": "sendgrid"},
        headers=api_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["suppressed"] is True

    c = service.get_contact("c1")
    assert c["suppressed"] is True


# ── Owner reply notification ────────────────────────────────────────────────
def _fake_httpx_client(
    response_json: dict | None = None,
    status: int = 200,
    exc: Exception | None = None,
):
    """Return a class that, when entered as a context manager, yields a mock httpx client."""
    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ARG002
            pass

        def __enter__(self):
            mock = MagicMock()
            if exc is not None:
                mock.post.side_effect = exc
            else:
                mock_response = MagicMock()
                mock_response.status_code = status
                mock_response.is_success = 200 <= status < 300
                mock_response.json.return_value = response_json or {}
                mock_response.text = str(response_json)
                mock.post.return_value = mock_response
            return mock

        def __exit__(self, *args, **kwargs):  # noqa: ARG002
            return False

    return FakeClient


def test_notify_owner_with_http_service(monkeypatch):
    notifications.reset()
    monkeypatch.setenv("NOTIFICATION_SERVICE_URL", "http://notify-svc")
    monkeypatch.setenv("NOTIFICATION_API_KEY", "notif-key")
    get_settings.cache_clear()

    captured: dict = {}

    class FakeClient(_fake_httpx_client({"ok": True}, 200)):
        def __enter__(self):
            mock = super().__enter__()
            original_post = mock.post

            def _capture_post(url, *, json, headers, **kwargs):  # noqa: ARG001
                captured["url"] = url
                captured["json"] = json
                captured["headers"] = headers
                return original_post(url, json=json, headers=headers)

            mock.post = _capture_post
            return mock

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = notifications.notify_owner_of_reply(
        "c1", "lo:amy", enrollment_id="e1", step_id="s1", channel="email",
        body="I want to apply now",
    )

    assert result["provider"] == "notification_http"
    assert result["ok"] is True
    assert captured["url"] == "http://notify-svc/notify"
    assert captured["headers"]["X-API-Key"] == "notif-key"
    assert captured["json"]["owner_id"] == "lo:amy"
    assert captured["json"]["kind"] == "reply"
    assert "I want to apply" in captured["json"]["message"]
    assert notifications.notification_log() == []


def test_notify_owner_falls_back_on_http_error(monkeypatch):
    notifications.reset()
    monkeypatch.setenv("NOTIFICATION_SERVICE_URL", "http://notify-svc")
    monkeypatch.setenv("NOTIFICATION_API_KEY", "notif-key")
    get_settings.cache_clear()

    monkeypatch.setattr(httpx, "Client", _fake_httpx_client(status=500))

    result = notifications.notify_owner_of_reply(
        "c1", "lo:amy", enrollment_id="e1", step_id="s1", channel="email",
    )

    assert result["provider"] == "notification_log"
    assert result["notified"] is True
    log = notifications.notification_log()
    assert len(log) == 1
    assert log[0]["owner_id"] == "lo:amy"


def test_notify_owner_without_service_url_logs():
    notifications.reset()
    result = notifications.notify_owner_of_reply(
        "c1", "lo:amy", enrollment_id="e1", step_id="s1", channel="email",
        body="I want to apply",
    )
    assert result["provider"] == "notification_log"
    assert result["notified"] is True
    assert "I want to apply" in result["payload"]["message"]


def test_notify_owner_without_owner_id_does_not_notify(monkeypatch):
    notifications.reset()
    monkeypatch.setenv("NOTIFICATION_SERVICE_URL", "http://notify-svc")
    get_settings.cache_clear()

    calls = []

    class FakeClient(_fake_httpx_client({"ok": True}, 200)):
        def __enter__(self):
            mock = MagicMock()
            mock.post.side_effect = lambda *a, **k: calls.append((a, k)) or MagicMock(
                status_code=200, is_success=True, json=lambda: {"ok": True}, text=""
            )
            return mock

    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = notifications.notify_owner_of_reply(
        "c1", "", enrollment_id="e1", step_id="s1", channel="email",
    )

    assert result["notified"] is False
    assert result["provider"] == "notification_log"
    assert calls == []


# ── A/B SHA-256 variant allocation ──────────────────────────────────────────
def test_abtest_allocate_deterministic_and_balanced():
    step = Step(id="s1", variants={"a": "Hello A", "b": "Hello B", "c": "Hello C"})

    # Same ids always produce the same variant.
    v1, _ = abtest.allocate(step, "e1")
    v2, _ = abtest.allocate(step, "e1")
    assert v1 == v2

    # Distribution is deterministic and roughly balanced across many enrollment ids.
    counts: dict[str, int] = {}
    for i in range(120):
        name, _ = abtest.allocate(step, f"enrollment-{i}")
        counts[name] = counts.get(name, 0) + 1
    for count in counts.values():
        assert 20 <= count <= 60  # within reasonable SHA-256 spread for 3 variants

    # No variants -> base template.
    base_step = Step(id="s1", template="base")
    name, template = abtest.allocate(base_step, "e1")
    assert name == ""
    assert template == "base"


def test_abtest_variant_matches_sha256_mod():
    step = Step(id="s1", variants={"a": "A", "b": "B"})
    eid = "e1"
    expected_index = int(hashlib.sha256(f"{step.id}:{eid}".encode()).hexdigest(), 16) % 2
    expected_name = sorted(step.variants)[expected_index]
    name, _ = abtest.allocate(step, eid)
    assert name == expected_name


def test_abtest_variant_used_in_send():
    _contact()
    _seq(steps=[Step(id="s1", channel="email", delay="PT0M",
                     template="base", variants={"a": "Hello A", "b": "Hello B"})])
    r = service.enroll("c1", "seq", now=DAY)
    eid = r["enrollment"]["enrollment_id"]
    service.tick(now=DAY)

    e = service.get_enrollment(eid)
    assert e["variant"] in ("a", "b")
    assert providers.sent_log[-1]["body"] in ("Hello A", "Hello B")


# ── Cross-agent CMS precheck client ─────────────────────────────────────────
def _cms_capturing_client(
    response: dict | None = None,
    status: int = 200,
    exc: Exception | None = None,
):
    captured: dict = {}

    class FakeClient(_fake_httpx_client(response, status, exc)):
        def __enter__(self):
            mock = super().__enter__()
            original_post = mock.post

            def _capture_post(url, *, json, headers, **kwargs):  # noqa: ARG001
                captured["url"] = url
                captured["json"] = json
                captured["headers"] = headers
                return original_post(url, json=json, headers=headers)

            mock.post = _capture_post
            return mock

    return FakeClient, captured


def test_compliance_precheck_maps_call_task_channel(monkeypatch):
    monkeypatch.setenv("COMPLIANCE_MONITOR_URL", "http://mock-cms")
    monkeypatch.setenv("FUS_PRECHECK_ENABLED", "true")
    get_settings.cache_clear()

    FakeClient, captured = _cms_capturing_client({"status": "allowed"}, 200)
    monkeypatch.setattr(httpx, "Client", FakeClient)

    contact = Contact(contact_id="c1", consent_basis="express_written")
    scheduled = datetime(2026, 8, 4, 14, 0, tzinfo=UTC)
    result = compliance_client.precheck(
        contact, "call_task", "Hi", scheduled, settings=get_settings(),
    )

    assert result == {"status": "allowed"}
    assert captured["json"]["channel"] == "call"
    assert captured["headers"]["X-Request-ID"]


def test_compliance_precheck_detects_unsubscribe_for_email(monkeypatch):
    monkeypatch.setenv("COMPLIANCE_MONITOR_URL", "http://mock-cms")
    monkeypatch.setenv("FUS_PRECHECK_ENABLED", "true")
    get_settings.cache_clear()

    FakeClient, captured = _cms_capturing_client({"status": "allowed"}, 200)
    monkeypatch.setattr(httpx, "Client", FakeClient)

    contact = Contact(contact_id="c1", consent_basis="express_written")
    result = compliance_client.precheck(
        contact, "email", "Reply to unsubscribe", DAY, settings=get_settings(),
    )

    assert result == {"status": "allowed"}
    assert captured["json"]["channel"] == "email"
    assert captured["json"]["has_unsubscribe"] is True


def test_compliance_precheck_x_request_id_is_uuid_hex(monkeypatch):
    monkeypatch.setenv("COMPLIANCE_MONITOR_URL", "http://mock-cms")
    monkeypatch.setenv("FUS_PRECHECK_ENABLED", "true")
    get_settings.cache_clear()

    FakeClient, captured = _cms_capturing_client({"status": "allowed"}, 200)
    monkeypatch.setattr(httpx, "Client", FakeClient)

    contact = Contact(contact_id="c1", consent_basis="express_written")
    compliance_client.precheck(contact, "email", "hi", DAY, settings=get_settings())

    x_request_id = captured["headers"]["X-Request-ID"]
    assert len(x_request_id) == 32
    int(x_request_id, 16)  # valid hex


def test_compliance_precheck_falls_back_to_api_key_when_no_compliance_key(monkeypatch):
    monkeypatch.setenv("COMPLIANCE_MONITOR_URL", "http://mock-cms")
    monkeypatch.setenv("FUS_PRECHECK_ENABLED", "true")
    monkeypatch.setenv("COMPLIANCE_API_KEY", "")
    get_settings.cache_clear()

    FakeClient, captured = _cms_capturing_client({"status": "allowed"}, 200)
    monkeypatch.setattr(httpx, "Client", FakeClient)

    contact = Contact(contact_id="c1", consent_basis="express_written")
    compliance_client.precheck(contact, "email", "hi", DAY, settings=get_settings())

    assert captured["headers"]["X-API-Key"] == get_settings().api_key


def test_compliance_precheck_disabled_or_unreachable_returns_none(monkeypatch):
    monkeypatch.setenv("COMPLIANCE_MONITOR_URL", "")
    monkeypatch.setenv("FUS_PRECHECK_ENABLED", "false")
    get_settings.cache_clear()

    contact = Contact(contact_id="c1", consent_basis="express_written")
    assert compliance_client.precheck(contact, "email", "hi", DAY, settings=get_settings()) is None
