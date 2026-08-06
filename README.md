# crm-operations-platform-service

**Operations Platform Service** — Feedback loop, performance analytics, compliance, task automation, follow-ups.

Part of the **CRM AI Agent Platform** (25 engines consolidated into 5 services). This service
groups 5 sub-agents; each keeps its original engine intact under `src/agents/<name>/` and runs as
its own service, fronted by a thin API gateway (`src/api/main.py`) that routes to each.

## Sub-agents
- **feedback_loop** ✅ — Initial implementation ready. Captures predictions, outcomes, overrides, and explicit feedback; builds point-in-time labels; monitors drift and performance; and orchestrates reproducible retraining with fairness-gated promotion and A/B testing (P0–P3 of the FLS ticket + CEAAPSS-559/560). See `src/agents/feedback_loop/README.md` for details.
- **performance_analytics** ✅ — Initial implementation ready. Governed KPIs, multi-touch attribution, pipeline funnels, rep/team scorecards, channel effectiveness, finance reconciliation, anomaly/threshold alerts, and scheduled CSV/PDF reports (P0–P3 of the PAS ticket). See `src/agents/performance_analytics/README.md` for details.
- **compliance_monitor** ← `Compliance-Monitoring` — Audit/governance + fair-lending.
- **task_automation** ← `Task-Automation` — Workflow automation engine.
- **follow_up_sequences** ← `Followup-Sequence` — Cadence / drip sequences.

## Architecture
- `src/agents/<name>/` — the original engine, copied verbatim (its own app/, deps, Dockerfile).
- `src/api/main.py` — gateway: `/<agent-slug>/*` proxies to that agent's service (URL from env).
- `docker-compose.yml` — one container per agent + the gateway (+ shared Postgres).
- `config/`, `src/{models,services,db,utils}/` — shared platform concerns.

## Run
```bash
cp .env.example .env
docker compose up --build         # gateway on :8080, each agent on its own service
```

- Gateway health: `GET /health` · agents map: `GET /agents`
