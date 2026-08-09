# crm-operations-platform-service

**Operations Platform Service** — Feedback loop, performance analytics, compliance, task automation, follow-ups.

Part of the **CRM AI Agent Platform** (25 engines consolidated into 5 services). This service
groups 5 sub-agents; each keeps its original engine intact under `src/agents/<name>/` and runs as
its own service, fronted by a thin API gateway (`src/api/main.py`) that routes to each.

## Sub-agents
- **feedback_loop** ✅ — Initial implementation ready. Captures predictions, outcomes, overrides, and explicit feedback; builds point-in-time labels; monitors drift and performance; and orchestrates reproducible retraining with fairness-gated promotion and A/B testing (P0–P3 of the FLS ticket + CEAAPSS-559/560). See `src/agents/feedback_loop/README.md` for details.
- **performance_analytics** ✅ — Initial implementation ready. Governed KPIs, multi-touch attribution, pipeline funnels, rep/team scorecards, channel effectiveness, finance reconciliation, anomaly/threshold alerts, and scheduled CSV/PDF reports (P0–P3 of the PAS ticket). P4 bug-audit fixes (ISO reconciliation, time-decay `funded_at`, thread-safe repositories) are in. See `src/agents/performance_analytics/README.md` for details.
- **compliance_monitor** ✅ — Initial implementation ready. Audit/governance + fair-lending: pre-send compliance hook, consent/DNC/quiet-hours, CAN-SPAM, immutable hash-chained audit, fairness/disparity, ECOA disclosure clocks, PII/retention, rule versioning. See `docs/implementation_status_cms.md` and `src/agents/compliance_monitor/README.md`.
- **task_automation** ✅ — Initial implementation ready. Workflow automation engine: JSONLogic/CEL rule engine, stage-driven task creation, document requests, business-hours SLA, idempotency, dry-run/canary/rollback, LLM guardrails. P4 bug-audit fixes (doc verification default, `trigger_id` assertion, thread-safe in-memory state) are in. See `docs/implementation_status_tas.md` and `src/agents/task_automation/README.md`.
- **follow_up_sequences** ✅ — Initial implementation ready. Cadence / drip sequences: sequence builder, enrollment/dedup, quiet-hours/frequency guardrails, engagement-driven branching, stop conditions, personalization, analytics. See `docs/implementation_status_fus.md` and `src/agents/follow_up_sequences/README.md`.

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
