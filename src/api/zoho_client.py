"""Zoho CRM record fetch for the static-body webhook relay.

The relay endpoint receives ONLY a static body; every dynamic value is
resolved here, inside the service, by pulling the triggering record from
Zoho CRM (record id arrives via the ``X-CRM-Record-Id`` header).

Credentials come from the environment (``ZOHO_CLIENT_ID``,
``ZOHO_CLIENT_SECRET``, ``ZOHO_REFRESH_TOKEN``, optional
``ZOHO_ACCOUNTS_URL`` / ``ZOHO_API_BASE``). Without credentials — or on any
Zoho error — ``get_record`` degrades to ``{"id": <record_id>}`` so journeys
still run with defaults instead of failing.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

_TOKEN: dict[str, Any] = {"value": None, "expires": 0.0}


def creds_present() -> bool:
    return all(
        os.environ.get(k)
        for k in ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN")
    )


async def _access_token(client: httpx.AsyncClient) -> str | None:
    now = time.time()
    if _TOKEN["value"] and now < _TOKEN["expires"]:
        return _TOKEN["value"]
    accounts = os.environ.get("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com").rstrip("/")
    try:
        resp = await client.post(
            accounts + "/oauth/v2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": os.environ["ZOHO_CLIENT_ID"],
                "client_secret": os.environ["ZOHO_CLIENT_SECRET"],
                "refresh_token": os.environ["ZOHO_REFRESH_TOKEN"],
            },
            timeout=20.0,
        )
        token = resp.json().get("access_token")
    except Exception:  # noqa: BLE001 — any failure degrades to no-fetch
        return None
    if not token:
        return None
    _TOKEN["value"] = token
    _TOKEN["expires"] = now + 45 * 60  # Zoho tokens last 60 min; refresh early
    return token


async def get_record(module: str, record_id: str) -> dict[str, Any]:
    """Fetch one record; on any failure return the minimal ``{"id": ...}``."""
    fallback = {"id": record_id}
    if not record_id or not creds_present():
        return fallback
    api = os.environ.get("ZOHO_API_BASE", "https://www.zohoapis.com").rstrip("/")
    async with httpx.AsyncClient() as client:
        token = await _access_token(client)
        if not token:
            return fallback
        try:
            resp = await client.get(
                f"{api}/crm/v8/{module}/{record_id}",
                headers={"Authorization": f"Zoho-oauthtoken {token}"},
                timeout=20.0,
            )
            data = resp.json().get("data") or []
        except Exception:  # noqa: BLE001
            return fallback
    if data and isinstance(data[0], dict):
        record = data[0]
        record.setdefault("id", record_id)
        return record
    return fallback
