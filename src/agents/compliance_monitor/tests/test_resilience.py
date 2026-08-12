"""Resilient cross-agent client + circuit breaker tests."""
import asyncio

import httpx
import pytest

from app.core.resilience import CircuitOpenError, ResilientClient


def test_resilient_client_retries_and_opens_circuit():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(503)

    async def _run():
        transport = httpx.MockTransport(handler)
        client = ResilientClient(
            httpx.AsyncClient(transport=transport),
            name="test",
            max_retries=2,
            failure_threshold=2,
            base_delay=0.01,
        )
        for _ in range(2):
            with pytest.raises(httpx.HTTPStatusError):
                await client.get("http://example.com/events")

        with pytest.raises(CircuitOpenError):
            await client.get("http://example.com/events")

        await client.aclose()
        return call_count

    count = asyncio.run(_run())
    assert count == 4  # 2 calls * 2 attempts each


def test_resilient_client_succeeds_and_records_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-request-id") == "rid-123"
        return httpx.Response(200, json={"events": []})

    async def _run():
        from structlog.contextvars import bind_contextvars

        bind_contextvars(request_id="rid-123")
        transport = httpx.MockTransport(handler)
        client = ResilientClient(
            httpx.AsyncClient(transport=transport),
            name="test",
            max_retries=2,
            base_delay=0.01,
        )
        resp = await client.get("http://example.com/events")
        await client.aclose()
        return resp.status_code, resp.json()

    status, data = asyncio.run(_run())
    assert status == 200
    assert data == {"events": []}
