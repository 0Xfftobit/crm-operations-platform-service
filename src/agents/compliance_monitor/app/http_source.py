"""HTTP-poll upstream source (EVENT_SOURCE_GATEWAY=http).

For deployments without a Kafka bus, poll a REST endpoint (``AUDIT_SOURCE_URL``) for activity events
and feed each to ``service.record_activity``. A monotonic cursor (last ``source_seq`` or a
server-supplied ``cursor``) advances so events aren't re-ingested — and event-id idempotency in the
service catches any overlap. Errors are logged and retried on the next tick (never fatal).
"""
import asyncio

import httpx
import structlog

from app.core.config import get_settings
from app.domain.models import ActivityEvent

logger = structlog.get_logger(__name__)


async def poll_once(client: httpx.AsyncClient, cursor: int) -> tuple[int, int]:
    """Fetch one page from the upstream; ingest events; return (ingested_count, new_cursor)."""
    settings = get_settings()
    resp = await client.get(f"{settings.audit_source_url.rstrip('/')}/events",
                            params={"since": cursor})
    resp.raise_for_status()
    data = resp.json()
    events = data.get("events", []) if isinstance(data, dict) else data
    count = 0
    for ev in events:
        service_record(ev)
        cursor = max(cursor, int(ev.get("source_seq") or 0)) or cursor + 1
        count += 1
    if isinstance(data, dict) and data.get("cursor") is not None:
        cursor = int(data["cursor"])
    return count, cursor


def service_record(ev: dict) -> None:
    from app import service
    service.record_activity(ActivityEvent(**ev))


async def poll_loop(stop: asyncio.Event) -> None:
    settings = get_settings()
    if settings.event_source_gateway != "http" or not settings.audit_source_url:
        return
    cursor = 0
    logger.info("http_source_started", url=settings.audit_source_url)
    async with httpx.AsyncClient(timeout=settings.audit_source_timeout) as client:
        while not stop.is_set():
            try:
                count, cursor = await poll_once(client, cursor)
                if count:
                    logger.info("http_source_ingested", count=count, cursor=cursor)
            except Exception as exc:  # noqa: BLE001 — poll failure is retried next tick
                logger.warning("http_source_poll_failed", error=str(exc))
            await asyncio.sleep(settings.http_poll_interval_s)
