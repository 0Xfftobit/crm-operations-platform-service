"""API gateway for crm-operations-platform-service.

Routes /<agent-slug>/* to that agent's own service (URL from <AGENT>_URL env). Each agent
stays a self-contained engine under src/agents/<name>/ with its own app + deps.
"""
from __future__ import annotations

import logging
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from config.settings import Settings, get_settings

# agent_folder -> url-slug
AGENTS = {
    "compliance_monitor": "compliance-monitor",
    "task_automation": "task-automation",
    "follow_up_sequences": "follow-up-sequences",
    "feedback_loop": "feedback-loop",
    "performance_analytics": "performance-analytics",
}

_HOP_BY_HOP = {
    "host",
    "content-encoding",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "upgrade",
}

logger = logging.getLogger(__name__)


def _upstream(agent: str, settings: Settings) -> str:
    return settings.agent_urls().get(agent, "")


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    """Gateway-level X-API-Key check. Mirrors the per-engine auth contract."""
    if not x_api_key or x_api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid API key",
        )


app = FastAPI(
    title="crm-operations-platform-service",
    summary="Feedback loop, performance analytics, compliance, task automation, follow-ups.",
)

# Restrictive CORS: configured origins only; wildcard rejected by Settings validator in prod.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/agents")
async def agents(settings: Settings = Depends(get_settings)) -> dict[str, dict[str, str | float]]:
    return {
        a: {
            "slug": s,
            "upstream": _upstream(a, settings) or "(unset)",
            "timeout": settings.agent_timeout(a),
        }
        for a, s in AGENTS.items()
    }


@app.get("/ready")
async def ready(settings: Settings = Depends(get_settings)) -> dict[str, str | dict[str, str]]:
    out: dict[str, str] = {}
    ready_ok = True
    async with httpx.AsyncClient(timeout=3.0) as c:
        for a in AGENTS:
            url = _upstream(a, settings)
            if not url:
                out[a] = "unset"
                ready_ok = False
                continue
            status_text: str | None = None
            # Probe /ready first so we surface dependency health, then fall back to /health.
            for hp in ("/api/v1/ready", "/api/v1/health", "/health", "/healthz"):
                try:
                    r = await c.get(url.rstrip("/") + hp)
                    if r.status_code == 200:
                        status_text = "ok"
                        break
                    status_text = f"http {r.status_code}"
                except Exception as e:  # noqa: BLE001
                    status_text = f"down: {type(e).__name__}"
            out[a] = status_text or "unknown"
            if status_text != "ok":
                ready_ok = False
    return {"status": "ok" if ready_ok else "degraded", "agents": out}


_SLUG_TO_AGENT = {s: a for a, s in AGENTS.items()}


@app.api_route(
    "/{slug}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    dependencies=[Depends(require_api_key)],
)
async def proxy(slug: str, path: str, request: Request, settings: Settings = Depends(get_settings)) -> Response:
    agent = _SLUG_TO_AGENT.get(slug)
    if not agent:
        return Response(status_code=404, content=f"unknown agent '{slug}'")
    # Request-id propagation / generation for end-to-end tracing.
    request_id = request.headers.get("x-request-id") or uuid4().hex

    base = _upstream(agent, settings)
    if not base:
        return Response(
            status_code=502,
            content=f"{agent} upstream not configured",
            headers={"x-request-id": request_id},
        )
    url = base.rstrip("/") + "/" + path
    body = await request.body()

    # Forward upstream headers except hop-by-hop ones; preserve auth/RLS headers.
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }
    headers["x-request-id"] = request_id

    timeout = settings.agent_timeout(agent)
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            up = await c.request(
                request.method,
                url,
                params=request.query_params,
                content=body,
                headers=headers,
            )
    except httpx.TimeoutException:
        logger.warning(
            "gateway_proxy_timeout",
            extra={"agent": agent, "url": url, "timeout": timeout},
        )
        return Response(status_code=504, content="upstream timeout")
    except httpx.ConnectError as e:
        logger.warning(
            "gateway_proxy_connect_error",
            extra={"agent": agent, "url": url, "error": str(e)},
        )
        return Response(status_code=502, content=f"upstream unreachable: {e}")

    response_headers = {
        k: v
        for k, v in up.headers.items()
        if k.lower() not in _HOP_BY_HOP and k.lower() != "content-length"
    }
    response_headers["x-request-id"] = request_id
    return Response(
        status_code=up.status_code,
        content=up.content,
        headers=response_headers,
    )
