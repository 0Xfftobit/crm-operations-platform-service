"""Resilient audit-source client used by the HTTP-poll upstream.

Propagates ``X-Request-ID`` from structlog contextvars and an optional ``X-API-Key``.
"""
from __future__ import annotations

from app.core.resilience import ResilientClient


class AuditClient(ResilientClient):
    """Client for the platform audit / event REST source."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 20.0,
        api_key: str | None = None,
    ) -> None:
        headers: dict[str, str] = {}
        if api_key:
            headers["x-api-key"] = api_key
        super().__init__(name="audit_source", timeout=timeout, headers=headers)
        self.base_url = base_url.rstrip("/")

    async def fetch_events(self, cursor: int = 0) -> dict:
        """GET /events?since={cursor} and return the JSON body."""
        resp = await self.get(f"{self.base_url}/events", params={"since": cursor})
        return resp.json()
