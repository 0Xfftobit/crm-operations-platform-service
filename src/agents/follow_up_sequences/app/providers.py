"""Channel orchestration (System Architecture §3/§5) — uniform send interface + retry.

Every channel (SMS/Twilio, email, WhatsApp Business API, voicemail drop) is reached through one
``deliver`` interface with retry-on-failure. The default is an in-memory **mock** provider that
records sends (so analytics + tests can inspect them); a deployment injects real providers via
``set_provider`` without changing callers. Persistent failure after ``max_attempts`` returns a
failed result (the service dead-letters it). Multi-provider failover slots in behind this interface.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.domain.models import Contact

# Recorded mock sends — inspectable by tests / analytics until real providers are wired.
sent_log: list[dict] = []


@dataclass
class ProviderResult:
    success: bool
    provider: str
    attempts: int = 1
    error: str = ""


def _mock_send(contact: Contact, channel: str, body: str) -> ProviderResult:
    sent_log.append({"contact_id": contact.contact_id, "tenant_id": contact.tenant_id,
                     "channel": channel, "body": body})
    return ProviderResult(True, f"mock-{channel}")


_provider = _mock_send


def set_provider(fn) -> None:
    """Inject a real provider callable ``(contact, channel, body) -> ProviderResult``."""
    global _provider
    _provider = fn


def reset() -> None:
    global _provider
    _provider = _mock_send
    sent_log.clear()


def deliver(contact: Contact, channel: str, body: str, *, max_attempts: int = 4) -> ProviderResult:
    """Send through the channel provider, retrying transient failures up to ``max_attempts``."""
    last = ProviderResult(False, "", 0, "not_attempted")
    for attempt in range(1, max_attempts + 1):
        try:
            res = _provider(contact, channel, body)
        except Exception as exc:  # noqa: BLE001 — a provider error is a transient failure to retry
            last = ProviderResult(False, f"provider-{channel}", attempt, str(exc))
            continue
        res.attempts = attempt
        if res.success:
            return res
        last = res
    return last
