"""Test fixtures for the Performance Analytics System."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.service import reset_state

API_KEY = "test-key"
BASE = "/api/v1"


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", API_KEY)
    monkeypatch.setenv("STORAGE_BACKEND", "memory")
    get_settings.cache_clear()
    reset_state()
    yield
    get_settings.cache_clear()
    reset_state()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), headers={"X-API-Key": API_KEY})


def touch_payload(
    contact_pid: str = "contact-1",
    deal_id: str | None = "deal-1",
    rep_id: str = "rep-1",
    team_id: str = "team-a",
    channel: str = "email",
    campaign_id: str | None = None,
    sent_at: str | None = None,
    engaged: bool = False,
    opened: bool = False,
    replied: bool = False,
    **kwargs: Any,
) -> dict:
    return {
        "touch_id": f"touch-{contact_pid}-{channel}",
        "contact_pid": contact_pid,
        "deal_id": deal_id,
        "rep_id": rep_id,
        "team_id": team_id,
        "tenant_id": "default",
        "channel": channel,
        "campaign_id": campaign_id,
        "sequence_id": kwargs.get("sequence_id"),
        "source": kwargs.get("source", channel),
        "sent_at": sent_at or dt.datetime.now(dt.UTC).isoformat(),
        "engaged": engaged,
        "opened": opened,
        "clicked": kwargs.get("clicked", False),
        "replied": replied,
        "opted_out": kwargs.get("opted_out", False),
        "delivery_status": "delivered",
        "segment": kwargs.get("segment"),
        "stage": kwargs.get("stage"),
    }


def outcome_payload(
    contact_pid: str = "contact-1",
    deal_id: str | None = "deal-1",
    stage: str = "lead",
    amount: float | None = None,
    occurred_at: str | None = None,
    rep_id: str = "rep-1",
    team_id: str = "team-a",
    **kwargs: Any,
) -> dict:
    return {
        "outcome_id": f"outcome-{deal_id}-{stage}",
        "contact_pid": contact_pid,
        "deal_id": deal_id,
        "rep_id": rep_id,
        "team_id": team_id,
        "tenant_id": "default",
        "stage": stage,
        "amount": amount,
        "occurred_at": occurred_at or dt.datetime.now(dt.UTC).isoformat(),
        "event_type": f"stage.{stage}",
        "source": kwargs.get("source"),
    }
