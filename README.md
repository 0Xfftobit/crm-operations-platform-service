# crm-operations-platform-service

**Operations Platform Service** — Feedback loop, performance analytics, compliance, task automation, follow-ups.

Part of the **CRM AI Agent Platform** (25 engines consolidated into 5 services). This service
groups 5 sub-agents; each keeps its original engine intact under `src/agents/<name>/` and runs as
its own service, fronted by a thin API gateway (`src/api/main.py`) that routes to each.

## Sub-agents
- **feedback_loop** ✅ — P0–P3 + P4 hardening. Captures predictions, outcomes, overrides, and explicit feedback; builds point-in-time labels; monitors drift and performance; and orchestrates reproducible retraining with fairness-gated promotion and A/B testing. Now includes PostgreSQL persistence, resilience client, circuit breaker, metrics, and Kafka contract bus. See `src/agents/feedback_loop/README.md`.
- **performance_analytics** ✅ — P0–P3 + P4 hardening. Governed KPIs, multi-touch attribution, pipeline funnels, rep/team scorecards, channel effectiveness, finance reconciliation, anomaly/threshold alerts, and scheduled CSV/PDF reports. Now includes PostgreSQL persistence, resilience client, circuit breaker, metrics, and Kafka contract bus. See `src/agents/performance_analytics/README.md`.
- **compliance_monitor** ✅ — P0–P3 + P4 hardening. Audit/governance + fair-lending: pre-send compliance hook, consent/DNC/quiet-hours, CAN-SPAM, immutable hash-chained audit, fairness/disparity, ECOA disclosure clocks, PII/retention, rule versioning. Includes SQLAlchemy/Postgres persistence, resilience, metrics, and Kafka contract bus. See `docs/implementation_status_cms.md` and `src/agents/compliance_monitor/README.md`.
- **task_automation** ✅ — P0–P3 + P4 hardening. Workflow automation engine: JSONLogic/CEL rule engine, stage-driven task creation, document requests, business-hours SLA, idempotency, dry-run/canary/rollback, LLM guardrails. Includes Postgres persistence, resilience client, circuit breaker, metrics, and Kafka contract bus. See `docs/implementation_status_tas.md` and `src/agents/task_automation/README.md`.
- **follow_up_sequences** ✅ — P0–P3 + P4 hardening. Cadence / drip sequences: sequence builder, enrollment/dedup, quiet-hours/frequency guardrails, engagement-driven branching, stop conditions, personalization, analytics. Now includes PostgreSQL persistence, resilience client, circuit breaker, metrics, and Kafka contract bus. See `docs/implementation_status_fus.md` and `src/agents/follow_up_sequences/README.md`.

## Architecture
- `src/agents/<name>/` — the original engine, copied verbatim (its own app/, deps, Dockerfile).
- `src/api/main.py` — gateway: `/<agent-slug>/*` proxies to that agent's service (URL from env).
- `docker-compose.yml` — one container per agent + the gateway (+ shared Postgres).
- `config/`, `src/{models,services,db,utils}/` — shared platform concerns.

## Current state (this session)

- **All five agents are implemented and tested** for P0–P3 + code-level P4 hardening.
- **Resilience & observability** are wired across the gateway and every agent:
  - `X-Request-ID` propagation and `X-API-Key` forwarding.
  - Prometheus-compatible `/metrics` (gateway) and `/api/v1/metrics` (agents).
  - `ResilientClient` retry + `CircuitBreaker` on cross-agent and third-party HTTP calls.
  - `/ready` health probes report `storage`, `postgres`, `redis`, and `kafka` status.
- **PostgreSQL/SQLite persistence** is implemented in all agents:
  - CMS, FUS, PAS, and FLS use `STORAGE_GATEWAY=postgres` + SQLAlchemy ORM + Alembic.
  - TAS uses `STORAGE_BACKEND=postgres` with inline schema creation.
  - Default is `memory`; SQLite works for local test smoke.
- **Kafka contract bus** is code-complete in all agents with in-memory fallback.
- **Test matrix**: 82 test modules, 663 unit/functional tests. See `docs/test_matrix.md` and `docs/testing.md`.

## Run

```bash
cp .env.example .env
# Infrastructure only (Postgres, Redis, Redpanda/Kafka, Prometheus, Grafana, OTel Collector)
docker compose --profile infra up -d

# Full stack (gateway on :8080, each agent on its own port)
docker compose up --build
```

- Gateway health: `GET /health` · readiness: `GET /ready` · agents map: `GET /agents`
