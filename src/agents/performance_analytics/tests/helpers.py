"""Shared test payload builders for the Performance Analytics System."""

from __future__ import annotations

import datetime as dt
from typing import Any


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
