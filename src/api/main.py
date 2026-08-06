"""API gateway for crm-operations-platform-service.

Routes /<agent-slug>/* to that agent's own service (URL from <AGENT>_URL env). Each agent
stays a self-contained engine under src/agents/<name>/ with its own app + deps.
"""
import os

import httpx
from fastapi import FastAPI, Request, Response

# agent_folder -> url-slug
AGENTS = {
    "compliance_monitor": "compliance-monitor",
    "task_automation": "task-automation",
    "follow_up_sequences": "follow-up-sequences"
}


def _upstream(agent: str) -> str:
    return os.environ.get(f"{agent.upper()}_URL", "")


app = FastAPI(title="crm-operations-platform-service", summary="Feedback loop, performance analytics, compliance, task automation, follow-ups.")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/agents")
async def agents():
    return {a: {"slug": s, "upstream": _upstream(a) or "(unset)"} for a, s in AGENTS.items()}


@app.get("/ready")
async def ready():
    out, ready_ok = {}, True
    async with httpx.AsyncClient(timeout=3.0) as c:
        for a in AGENTS:
            url = _upstream(a)
            if not url:
                out[a] = "unset"; ready_ok = False; continue
            status = None
            for hp in ("/api/v1/health", "/health", "/healthz"):
                try:
                    r = await c.get(url.rstrip("/") + hp)
                    if r.status_code == 200:
                        status = "ok"; break
                    status = f"http {r.status_code}"
                except Exception as e:  # noqa: BLE001
                    status = f"down: {type(e).__name__}"
            out[a] = status
            if status != "ok":
                ready_ok = False
    return {"status": "ok" if ready_ok else "degraded", "agents": out}


_SLUG_TO_AGENT = {s: a for a, s in AGENTS.items()}


@app.api_route("/{slug}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(slug: str, path: str, request: Request):
    agent = _SLUG_TO_AGENT.get(slug)
    if not agent:
        return Response(status_code=404, content=f"unknown agent '{slug}'")
    base = _upstream(agent)
    if not base:
        return Response(status_code=502, content=f"{agent} upstream not configured")
    url = base.rstrip("/") + "/" + path
    body = await request.body()
    async with httpx.AsyncClient(timeout=30.0) as c:
        up = await c.request(request.method, url, params=request.query_params,
                             content=body, headers={k: v for k, v in request.headers.items()
                                                    if k.lower() != "host"})
    return Response(status_code=up.status_code, content=up.content,
                    headers={"content-type": up.headers.get("content-type", "application/json")})
