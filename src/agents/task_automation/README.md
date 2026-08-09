# Task Automation Engine

Automatically generates, assigns, and schedules CRM tasks based on lead events, stage changes, or predefined workflow rules — ensuring agents always know what to do next.

## Goals

- Guarantee no lead is missed due to human error
- Standardize follow-up processes across all agents
- Reduce cognitive load — agents focus on doing, not planning
- Maintain a full auditable activity trail per lead

## Quick start

```bash
cd Task-Automation
cp .env.example .env
uv sync
chmod +x run.sh
./run.sh
```

- Swagger UI: http://localhost:8011/docs
- Health: http://localhost:8011/api/v1/health

## Docker

```bash
docker compose up --build
```

→ http://localhost:8011/docs

## Features

| Requirement | Implementation |
|-------------|----------------|
| Workflow rule builder | `POST /api/v1/rules` — trigger + conditions → task |
| Task types | call, whatsapp, send_email, send_document, schedule_meeting |
| Business-hours due dates | `app/business_hours.py` — timezone + Mon–Fri 9–17 |
| Auto-assignment | Lead owner from event payload (`agent_id`, `assigned_lo_id`) |
| Agent task queue | `GET /api/v1/dashboard/agent/{id}` — sorted by priority + due |
| Overdue alert | `overdue: true`, `alert_level: "red"` on tasks and dashboard |
| Recurring tasks | Rule `recurring` + scheduler spawns next instance |
| Completion logging | `POST /api/v1/tasks/{id}/complete` with outcome notes |
| Conflict detection | Dedupe same lead+type within `DUPLICATE_TASK_WINDOW_HOURS` |
| Audit trail | `GET /api/v1/audit/{lead_id}` |
| Manager view | `GET /api/v1/dashboard/manager` |

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Liveness |
| GET | `/api/v1/ready` | Readiness |
| GET/POST | `/api/v1/rules` | Workflow rules CRUD |
| POST | `/api/v1/events/ingest` | Trigger event → auto-create tasks |
| GET | `/api/v1/tasks` | List CRM tasks |
| POST | `/api/v1/tasks/{id}/complete` | Complete with outcome notes |
| GET | `/api/v1/dashboard/agent/{id}` | Agent queue |
| GET | `/api/v1/dashboard/manager` | Manager overview |
| GET | `/api/v1/audit/{lead_id}` | Lead activity trail |

Header: `X-API-Key: change-me-dev-key`

## Example workflow

```bash
# 1. Create rule: stage → underwriting → call in 2 business hours
curl -s -X POST http://localhost:8011/api/v1/rules \
  -H "X-API-Key: change-me-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Underwriting follow-up call",
    "trigger_event": "deal.stage_changed",
    "conditions": [{"field": "stage", "operator": "eq", "value": "underwriting"}],
    "task_type": "call",
    "delay_hours": 2,
    "assignee_strategy": "lead_owner",
    "priority": "high"
  }' | jq .

# 2. Ingest stage change event (or receive from Stage Tracking)
curl -s -X POST http://localhost:8011/api/v1/events/ingest \
  -H "X-API-Key: change-me-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "deal.stage_changed",
    "payload": {"lead_id": "L-1001", "stage": "underwriting", "agent_id": "lo:amy"}
  }' | jq .

# 3. Agent dashboard
curl -s http://localhost:8011/api/v1/dashboard/agent/lo:amy \
  -H "X-API-Key: change-me-dev-key" | jq .

# 4. Complete task
curl -s -X POST http://localhost:8011/api/v1/tasks/crm_abc123/complete \
  -H "X-API-Key: change-me-dev-key" \
  -H "Content-Type: application/json" \
  -d '{"outcome_notes": "Borrower confirmed docs sent", "outcome": "connected"}' | jq .
```

## Integrations

| System | Env | Purpose |
|--------|-----|---------|
| Stage Tracking | `STAGE_TRACKING_URL` | `deal.stage_changed` events |
| Lead Assignment | `LEAD_ASSIGNMENT_URL` | `lead.assigned` events |

During development, mock events via `POST /events/ingest`.

## Tests

```bash
uv run pytest tests/ -q          # 78 passed, 1 skipped
uv run ruff check app tests
```

## Latest P4 Hardening

- Bug-audit fixes: `doc_request_chain.complete_verification` defaults to `verified=False`; `POST /events/document-received` validates `workflow_id` and rejects non-string ids; `normalize_trigger`, `run_pipeline_stages`, `process_event`, and `_persist_and_emit` assert `trigger_id` is a `str`.
- In-memory global state is now protected by `threading.RLock()` across `app/store.py`, `app/runs.py`, `app/ledger.py`, `app/events.py`, `app/event_history.py`, `app/dlq.py`, `app/metrics.py`, `app/guards.py`, `app/escalation.py`, `app/cron_triggers.py`, `app/kafka_consumer.py`, `app/kafka_consumer_group.py`, `app/messaging/kafka_bus.py`, `app/messaging/trigger_ingestor.py`, `app/integrations/temporal.py`, `app/integrations/activity_runner.py`, `app/integrations/feature_store.py`, `app/workflows/doc_request_chain.py`, `app/tracing.py`, `app/agents/llm_task_agent.py`, `app/persistence/memory_store.py`, and `app/persistence/redis_store.py`.
- `tests/test_bug_audit.py` adds 6 regression tests.

## System Architecture

The engine follows the architecture spec: event-driven ingest → enrichment → rule engine → idempotent action execution → immutable ledger.

| Component | Module |
|-----------|--------|
| Trigger Ingestor | `app/messaging/trigger_ingestor.py` |
| Enrichment Resolver | `app/enrichment.py` + `app/integrations/feature_store.py` |
| Rule Engine | `app/engine/rule_engine.py`, `conflict_resolver.py` |
| Action Executor | `app/engine/action_executor.py` |
| Workflow Orchestrator | `app/integrations/temporal.py` |
| Idempotency Store | `app/persistence/redis_store.py` |
| Action Ledger | `app/ledger.py` + `app/persistence/db.py` |
| DLQ Manager | `app/dlq.py` |

```bash
curl -s http://localhost:8011/api/v1/architecture -H "X-API-Key: change-me-dev-key" | jq .
curl -s http://localhost:8011/api/v1/architecture/observability -H "X-API-Key: change-me-dev-key" | jq .
```

Set `STORAGE_BACKEND=sqlite` for Postgres-schema persistence in dev.

## Vision alignment

The Task Automation Engine is the **execution arm of Engine 2** — it **acts** where other sub-agents decide or predict.

| Vision capability | Implementation |
|-------------------|----------------|
| Event-triggered task creation | `POST /events/ingest` + workflow rules |
| CRM record hygiene | `update_record` / `compound` actions → `record.updated` |
| Document-request orchestration | `request_document` → `document.requested` (Engine 3/5) |
| Rule versioning & simulation | `version`, `rollout_status`, `/rules/{id}/simulate`, `/publish` |
| Workflow orchestration | Temporal `DocRequestChainWorkflow` + `GenericStartWorkflow` |
| LLM task agents (guardrailed) | `use_llm_notes` → draft notes, no compliance decisions |
| Idempotency (0 duplicate writes) | `X-Idempotency-Key` header |
| Immutable action ledger | `GET /vision/ledger` — rule id, inputs, outputs, provenance |
| Dead-letter queue | `GET /vision/dlq` |
| KPI tracking | `GET /vision/kpis` — latency p95, doc accuracy, SLA breaches |

### Vision API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/vision` | Mission, scope, peer engine map |
| GET | `/api/v1/vision/kpis` | Success metrics vs targets |
| GET | `/api/v1/vision/ledger` | Immutable action ledger |
| GET | `/api/v1/vision/events` | Emitted `task.created`, `document.requested`, etc. |
| GET | `/api/v1/vision/dlq` | Failed action dead-letter queue |
| POST | `/api/v1/vision/dlq/{id}/replay` | One-click DLQ replay (SRE role) |
| GET | `/api/v1/requirements` | Project requirements status matrix |

### Project Requirements (Engine 2 sub-agent)

| Requirement | Implementation |
|-------------|----------------|
| Kafka trigger ingestion | HTTP adapter + optional background consumer (`KAFKA_CONSUMER_ENABLED=true`) |
| Cron schedules | `POST /triggers/schedules`, `/fire` |
| JSONLogic conditions | `jsonlogic` field on rules |
| Full action library | `create_task`, `assign_owner`, `update_record`, `request_document`, `post_note`, `start_workflow`, `emit_event` |
| Idempotency `(trigger,rule,action)` | Per-action dedupe + `X-Idempotency-Key` |
| Canary rollout (1%) | `draft` → `canary` → `active` via `/rules/{id}/publish` |
| Mandatory dry-run | Simulate before first publish (409 if skipped) |
| Loop guard + action budget | `MAX_ACTIONS_PER_DEAL`, cycle detection |
| LLM confidence + approval | `LLM_CONFIDENCE_THRESHOLD`, `POST /agents/llm/approve` |
| RBAC | `X-Role` header — revops, engineer, sre, compliance |
| Ledger inputs hash | Every write action includes `inputs_hash` |

```bash
# Requirements matrix
curl -s http://localhost:8011/api/v1/requirements -H "X-API-Key: change-me-dev-key" | jq .
curl -s http://localhost:8011/api/v1/functional-requirements -H "X-API-Key: change-me-dev-key" | jq .

# Gateway-style automation API
curl -s -X POST http://localhost:8011/automation/v1/triggers \
  -H "X-API-Key: change-me-dev-key" -H "Content-Type: application/json" \
  -d '{"topic":"deal.stage_changed","payload":{"lead_id":"L-1","stage":"pre_approval","processor_id":"proc:amy"}}' | jq .
```

### Compound rule example (Pre-Approval)

```bash
curl -X POST http://localhost:8011/api/v1/rules \
  -H "X-API-Key: change-me-dev-key" -H "Content-Type: application/json" \
  -d '{
    "name": "Pre-approval automation bundle",
    "trigger_event": "deal.stage_changed",
    "conditions": [{"field": "stage", "operator": "eq", "value": "pre_approval"}],
    "action_type": "compound",
    "task_type": "send_document",
    "priority": "urgent",
    "record_updates": {"flags": ["docs_pending"]},
    "auto_document_set": true
  }'
```

## System design

Task Automation implements the full **Task Automation Engine** system design:

| Component | Module | Role |
|-----------|--------|------|
| Trigger Ingestor | `app/messaging/trigger_ingestor.py` | Canonical envelope + event-id dedupe |
| Rule Engine | `app/engine/action_executor.py` | JSONLogic conditions, conflict resolution |
| Action Executor | `app/engine/action_executor.py` | Idempotent connector calls + ledger |
| LLM Task Agent | `app/agents/llm_task_agent.py` | Drafts with confidence + approval gate |
| Workflow Orchestrator | `app/workflows/doc_request_chain.py` | Doc request → signal → verify → complete |

### Automation API (`/automation/v1`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/automation/v1/triggers` | Fire ad-hoc trigger |
| POST | `/automation/v1/rules/{id}/simulate` | Dry-run with `{lookback_days, sample_size}` |
| GET | `/automation/v1/runs/{runId}` | Run status + executed actions |
| GET | `/api/v1/system-design` | Components, Redis keys, config, testing strategy |

### Configuration

| Parameter | Default | Env var |
|-----------|---------|---------|
| Per-deal action budget | 25 | `MAX_ACTIONS_PER_DEAL` |
| LLM confidence threshold | 0.85 | `LLM_CONFIDENCE_THRESHOLD` |
| Default task SLA | 48h | `DEFAULT_TASK_SLA_HOURS` |
| Idempotency TTL | 7d | `IDEMPOTENCY_TTL_DAYS` |
| Loop budget TTL | 1h | `LOOP_BUDGET_TTL_HOURS` |

Redis keys: `idem:{trigger_id}:{rule_id}:{action_key}`, `loopbudget:{deal_id}`, `rule:{id}:v{version}`.

### Postgres + Redis (production persistence)

```bash
# Start infrastructure
docker compose up -d postgres redis

# Local dev against real stores
export STORAGE_BACKEND=postgres
export DATABASE_URL=postgresql://taskauto:taskauto@localhost:5433/task_automation
export IDEMPOTENCY_BACKEND=redis
export REDIS_URL=redis://localhost:6379/0
./run.sh
```

Or use full stack: `docker compose up --build` (app wired to postgres + redis automatically).

Ledger enforces `UNIQUE(trigger_id, rule_id, action_key)` — duplicate actions return `outcome: deduped` without a second row.

### Temporal connector activities

Connector calls (`assign_owner`, `update_record`, `post_note`, `request_document`, `create_calendar_event`) run through Temporal activities with retry/backoff from config.

### Doc-request workflow (`DocRequestChainWorkflow`)

Multi-step chain with durable SLA timers and signals:

`AwaitingDocs` → (`Escalated` on SLA timeout) → `Verifying` → `Completed`

| Signal | Effect |
|--------|--------|
| `document_received` | Appends signal; moves to Verifying when all required doc types received |
| `compensate` | Cancels with compensation record |

Local fallback (`provider: local`) when `TEMPORAL_ENABLED=false` (default in dev/tests).

**Local dev (default):** activities execute in-process with the same retry policy (`RETRY_MAX_ATTEMPTS`, `RETRY_BACKOFF_*`).

**Production Temporal:**
```bash
# Terminal 1 — Temporal worker
uv run python scripts/temporal_worker.py

# Terminal 2 — API with Temporal client enabled
export TEMPORAL_ENABLED=true
export TEMPORAL_HOST=localhost:7233
export TEMPORAL_TASK_QUEUE=task-automation
./run.sh
```

Inspect activity status: `GET /api/v1/architecture/components` → `temporal_activities`.

## Dependencies

- **Build**: Standalone (events mockable)
- **Runtime**: Stage Tracking, Lead Assignment
- **Feeds**: Follow-up Sequences, Performance Analytics, Next-Best-Action
