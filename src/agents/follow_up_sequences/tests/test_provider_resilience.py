"""Provider retry, exponential backoff, and rate-limit handling (FR-019)."""
from __future__ import annotations

import pytest

from app import providers
from app.domain.models import Contact


def _make_provider(*, rate_limit: bool = False, fail_count: int = 0, final_success: bool = True):
    """Return a controllable provider and a call-recorder list."""
    calls: list[tuple[str, str]] = []

    def provider(contact: Contact, channel: str, body: str) -> providers.ProviderResult:
        calls.append((channel, body))
        if len(calls) <= fail_count:
            if rate_limit:
                raise providers.ProviderRateLimit("rate limited")
            raise RuntimeError("transient failure")
        if final_success:
            return providers.ProviderResult(True, "mock", attempts=len(calls))
        return providers.ProviderResult(False, "mock", attempts=len(calls), error="permanent")

    return provider, calls


@pytest.fixture
def stub_time(monkeypatch):
    """Capture ``time.sleep`` calls inside ``app.providers`` to keep tests fast."""
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(providers.time, "sleep", fake_sleep)
    return sleeps


def test_deliver_retries_rate_limit_and_succeeds(stub_time):
    provider, calls = _make_provider(rate_limit=True, fail_count=2, final_success=True)
    providers.set_provider(provider)

    contact = Contact(contact_id="c1")
    result = providers.deliver(contact, "sms", "hello", max_attempts=4)

    assert result.success is True
    assert result.provider == "mock"
    assert result.attempts == 3
    assert len(calls) == 3
    # Exponential backoff: 2^attempt * 0.5 for the first two failures.
    assert stub_time == [1.0, 2.0]


def test_deliver_exhausts_attempts_on_rate_limit(stub_time):
    provider, calls = _make_provider(rate_limit=True, fail_count=4, final_success=False)
    providers.set_provider(provider)

    contact = Contact(contact_id="c1")
    result = providers.deliver(contact, "email", "hello", max_attempts=4)

    assert result.success is False
    assert result.provider == "provider-email"
    assert result.attempts == 4
    assert result.error == "rate_limited"
    assert len(calls) == 4
    assert stub_time == [1.0, 2.0, 4.0, 8.0]


def test_deliver_retries_transient_error_and_succeeds(stub_time):
    provider, calls = _make_provider(rate_limit=False, fail_count=1, final_success=True)
    providers.set_provider(provider)

    contact = Contact(contact_id="c1")
    result = providers.deliver(contact, "whatsapp", "hello", max_attempts=3)

    assert result.success is True
    assert result.attempts == 2
    assert len(calls) == 2
    assert stub_time == [1.0]


def test_deliver_retries_failed_provider_result(stub_time):
    # The provider returns a failed (but non-exception) result; deliver still backoffs and retries.
    provider, calls = _make_provider(rate_limit=False, fail_count=0, final_success=False)
    providers.set_provider(provider)

    contact = Contact(contact_id="c1")
    result = providers.deliver(contact, "voicemail", "hello", max_attempts=3)

    assert result.success is False
    assert result.attempts == 3
    assert result.error == "permanent"
    assert len(calls) == 3
    # A sleep follows every failed attempt, including the final exhausted one.
    assert stub_time == [1.0, 2.0, 4.0]


def test_deliver_caps_backoff_at_30_seconds(stub_time):
    # Force more failures than the cap allows; backoff should not exceed 30s.
    provider, calls = _make_provider(rate_limit=True, fail_count=10, final_success=False)
    providers.set_provider(provider)

    contact = Contact(contact_id="c1")
    max_attempts = 10
    providers.deliver(contact, "sms", "hello", max_attempts=max_attempts)

    assert all(s <= 30.0 for s in stub_time)
    # Attempt 7 would sleep 2^7 * 0.5 = 64s, so it and later should be capped at 30s.
    assert any(s == 30.0 for s in stub_time)
