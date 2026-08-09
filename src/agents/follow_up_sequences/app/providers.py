"""Channel orchestration (System Architecture §3/§5) — uniform send interface + retry.

Every channel (SMS/Twilio, email, WhatsApp Business API, voicemail drop) is reached through one
``deliver`` interface with retry-on-failure. Real providers are activated by environment variables;
if credentials are missing the engine falls back to the mock provider so tests and local runs keep
working. The ``_provider`` callable can still be overridden via ``set_provider``.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from threading import RLock
from typing import Any

import httpx

from app.core.config import get_settings
from app.domain.models import Contact

logger = logging.getLogger(__name__)

# Recorded mock sends — inspectable by tests / analytics until real providers are wired.
sent_log: list[dict] = []
_lock = RLock()


@dataclass
class ProviderResult:
    success: bool
    provider: str
    attempts: int = 1
    error: str = ""


def _mock_send(contact: Contact, channel: str, body: str) -> ProviderResult:
    with _lock:
        sent_log.append({"contact_id": contact.contact_id, "tenant_id": contact.tenant_id,
                         "channel": channel, "body": body})
    return ProviderResult(True, f"mock-{channel}")


def _twilio_send(contact: Contact, body: str, settings: Any) -> ProviderResult:
    sid = settings.twilio_account_sid
    token = settings.twilio_auth_token
    from_number = settings.twilio_from_number
    if not sid or not token or not from_number:
        return _mock_send(contact, "sms", body)
    to = contact.phone or contact.contact_id
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                auth=(sid, token),
                data={"From": from_number, "To": to, "Body": body},
            )
        if resp.status_code == 429:
            raise ProviderRateLimit("twilio rate limited")
        if resp.is_success:
            return ProviderResult(True, "twilio")
        return ProviderResult(False, "twilio", error=resp.text)
    except ProviderRateLimit:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("twilio_send_failed", contact=contact.contact_id, error=str(exc))
        return ProviderResult(False, "twilio", error=str(exc))


def _sendgrid_send(contact: Contact, body: str, settings: Any) -> ProviderResult:
    key = settings.sendgrid_api_key
    from_email = settings.sendgrid_from_email
    if not key or not from_email:
        return _mock_send(contact, "email", body)
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "personalizations": [{"to": [{"email": contact.email or contact.contact_id}]}],
                    "from": {"email": from_email},
                    "subject": body[:80],
                    "content": [{"type": "text/plain", "value": body}],
                },
            )
        if resp.status_code == 429:
            raise ProviderRateLimit("sendgrid rate limited")
        if resp.is_success:
            return ProviderResult(True, "sendgrid")
        return ProviderResult(False, "sendgrid", error=resp.text)
    except ProviderRateLimit:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("sendgrid_send_failed", contact=contact.contact_id, error=str(exc))
        return ProviderResult(False, "sendgrid", error=str(exc))


def _whatsapp_send(contact: Contact, body: str, settings: Any) -> ProviderResult:
    url = settings.whatsapp_api_url
    token = settings.whatsapp_api_token
    if not url or not token:
        return _mock_send(contact, "whatsapp", body)
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"to": contact.whatsapp or contact.contact_id, "body": body},
            )
        if resp.status_code == 429:
            raise ProviderRateLimit("whatsapp rate limited")
        if resp.is_success:
            return ProviderResult(True, "whatsapp")
        return ProviderResult(False, "whatsapp", error=resp.text)
    except ProviderRateLimit:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("whatsapp_send_failed", contact=contact.contact_id, error=str(exc))
        return ProviderResult(False, "whatsapp", error=str(exc))


def _voicemail_send(contact: Contact, body: str) -> ProviderResult:
    # Voicemail-drop / call-task providers require third-party integrations; mock until configured.
    logger.info("voicemail_mock", contact=contact.contact_id)
    return _mock_send(contact, "voicemail", body)


class ProviderRateLimit(Exception):
    pass


_PROVIDER_MAP = {
    "sms": _twilio_send,
    "email": _sendgrid_send,
    "whatsapp": _whatsapp_send,
    "voicemail": _voicemail_send,
    "call_task": _voicemail_send,
}


def _default_provider(contact: Contact, channel: str, body: str) -> ProviderResult:
    settings = get_settings()
    sender = _PROVIDER_MAP.get(channel)
    if sender is None:
        return _mock_send(contact, channel, body)
    if sender in (_twilio_send, _sendgrid_send, _whatsapp_send):
        return sender(contact, body, settings)
    return sender(contact, body)


_provider = _default_provider


def set_provider(fn) -> None:
    """Inject a real provider callable ``(contact, channel, body) -> ProviderResult``."""
    global _provider
    with _lock:
        _provider = fn


def reset() -> None:
    global _provider
    with _lock:
        _provider = _default_provider
        sent_log.clear()


def deliver(contact: Contact, channel: str, body: str, *, max_attempts: int = 4) -> ProviderResult:
    """Send through the channel provider with retry and exponential backoff for 429s."""
    with _lock:
        provider = _provider
    last = ProviderResult(False, "", 0, "not_attempted")
    for attempt in range(1, max_attempts + 1):
        try:
            res = provider(contact, channel, body)
        except ProviderRateLimit:
            last = ProviderResult(False, f"provider-{channel}", attempt, "rate_limited")
            time.sleep(min(2 ** attempt * 0.5, 30.0))
            continue
        except Exception as exc:  # noqa: BLE001 — a provider error is a transient failure to retry
            last = ProviderResult(False, f"provider-{channel}", attempt, str(exc))
            time.sleep(min(2 ** attempt * 0.5, 30.0))
            continue
        res.attempts = attempt
        if res.success:
            return res
        last = res
        # Exponential backoff for non-terminal failures; 429s are handled above.
        time.sleep(min(2 ** attempt * 0.5, 30.0))
    return last
