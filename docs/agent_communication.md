# Agent-to-Agent Communication & Orchestration

This document describes how the five CRM AI agent engines in `src/agents/` communicate with each other and with the rest of the platform.

## 1. Communication Patterns

The platform uses two complementary patterns:

1. **Synchronous REST via the API gateway** — request/response calls between engines (e.g., a pre-send compliance check, triggering an automation, querying analytics).
2. **Asynchronous events via an in-process bus + Kafka stubs** — each engine emits domain events; in local/dev mode they are kept in-process, and in production they can be mirrored onto a Kafka topic.

## 2. API Gateway (HTTP Routing)

`src/api/main.py` is the central HTTP gateway. It:

- Maps each agent to a URL slug:
  - `compliance_monitor` → `/compliance-monitor/*`
  - `task_automation` → `/task-automation/*`
  - `follow_up_sequences` → `/follow-up-sequences/*`
  - `feedback_loop` → `/feedback-loop/*`
  - `performance_analytics` → `/performance-analytics/*`
- Proxies all verbs (GET, POST, PUT, PATCH, DELETE) to the upstream agent service.
- Provides `/health`, `/ready`, and `/agents` discovery endpoints.
- Reads upstream URLs from environment variables (`COMPLIANCE_MONITOR_URL`, `TASK_AUTOMATION_URL`, `FOLLOW_UP_SEQUENCES_URL`, `FEEDBACK_LOOP_URL`, `PERFORMANCE_ANALYTICS_URL`).

The `docker-compose.yml` already wires all five services behind the gateway, so a single `docker compose up` starts the full platform with cross-agent HTTP routing.

## 3. Event Bus

Each agent has its own lightweight event module:

| Agent | Event module | Produced events (examples) | Kafka integration |
|-------|--------------|----------------------------|-------------------|
| `compliance_monitor` | `app/events.py` | `compliance.alert`, `compliance.case.opened`, `compliance.case.updated`, `compliance.signal` | Mirrors to `app/kafka_bus.py` when `KAFKA_PRODUCE=true` |
| `follow_up_sequences` | `app/events.py` | `sequence.enrolled`, `outreach.sent`, `outreach.failed`, `sequence.stopped`, `sequence.completed` | In-process bus only (Kafka stub deferred) |
| `task_automation` | `app/events.py` | `task.created`, `record.updated`, `document.requested`, `automation.run.completed`, `automation.failed` | Publishes to `app/messaging/kafka_bus.py` (in-process log) |
| `feedback_loop` | `app/service.py` event logs | Model retrain/promotion events | In-process log |
| `performance_analytics` | `app/service.py` event logs | Metric/reconciliation events | In-process log |

### 3.1 Kafka

- `compliance_monitor` can produce to and consume from Kafka (`KAFKA_PRODUCE`, `KAFKA_INGEST`, `KAFKA_TOPIC`).
- `task_automation` has a `messaging/kafka_bus.py` with canonical CRM topics and an in-process `_kafka_log`.
- `follow_up_sequences`, `feedback_loop`, and `performance_analytics` keep events in memory for local/dev use.

To enable real cross-agent eventing in production, start a Kafka broker and set `KAFKA_PRODUCE=true` / `KAFKA_INGEST=true` as appropriate. No code changes are required; the bus modules already degrade gracefully when the broker is unavailable.

## 4. Shared Platform Services

The `src/services/` package contains stubs for:

- `crm_service.py` — CRM record/task mutations.
- `data_service.py` — data access / enrichment.
- `notification_service.py` — outbound notifications.

These are currently placeholders. The Task Automation engine has its own local `app/integrations/crm.py` and `app/integrations/documents.py` stubs for the same contracts. A future P4 integration would unify these behind the platform service interfaces.

## 5. Cross-Agent Integration Points by Ticket

### 5.1 Compliance Monitor System (CMS)

- **Pre-send hook** (`POST /api/v1/precheck`) can be called by the Follow-up Sequences Engine (or any sender) before any outbound message.
- **Activity ingest** (`POST /api/v1/activity`) receives `record.updated`, `lead.scored`, `nba.recommended`, `deal.stage_changed`, etc.
- **Engine-8 signals** (`compliance.signal`) are produced on critical findings.
- **Policy ingestion** (`POST /api/v1/policies`) accepts Engine-8 thresholds.

### 5.2 Follow-up Sequences Engine (FUS)

- **Consumes**: `lead.scored`, `deal.stage_changed`, `contact.replied`, `consent.updated` (currently via direct service calls / test fixtures; Kafka consumer stub deferred).
- **Produces**: `sequence.enrolled`, `outreach.sent`, `outreach.failed`, `sequence.stopped`, `sequence.completed`.
- **Guardrails**: in-process consent/DNC/quiet-hours/frequency-cap checks; can be augmented by calling `POST /compliance-monitor/api/v1/precheck` through the gateway.

### 5.3 Task Automation Engine (TAS)

- **Consumes**: `deal.stage_changed`, `lead.scored`, `nba.recommended`, `task.completed`, `document.received`.
- **Produces**: `task.created`, `record.updated`, `document.requested`, `automation.run.completed`, `automation.failed`.
- **CRM connector**: `app/integrations/crm.py` stub for record/task mutations.
- **Document connector**: `app/integrations/documents.py` stub for `document.requested` events.
- **LLM task agent**: `app/agents/llm_task_agent.py` is a stub with confidence/approval guardrails; swapping to a real LLM provider requires only an adapter change.

## 6. Deployment Topology

The `docker-compose.yml` defines:

```
gateway (port 8080)
├── compliance_monitor:8000
├── task_automation:8000
├── follow_up_sequences:8000
├── feedback_loop:8000
└── performance_analytics:8000
```

Each agent is independently deployable. The gateway gives clients a single entry point, and `*_URL` environment variables make the mesh configurable. The default compose also enables the cross-agent HTTP fan-outs:

- `FUS_PRECHECK_ENABLED=true` + `COMPLIANCE_MONITOR_URL=http://compliance_monitor:8000`
- `OUTCOME_FANOUT_ENABLED=true` + `FEEDBACK_LOOP_URL=http://feedback_loop:8000`
- `FUS_FANOUT_ENABLED=true` + `FOLLOW_UP_SEQUENCES_URL=http://follow_up_sequences:8000`

## 7. Verification

Root integration tests pass:

```bash
cd /Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service
PYTHONPATH=. uv run pytest tests -v
```

Result: `2 passed` (`/health` and `/agents` discovery).

## 8. Known Gaps & P4 Items

- There is no shared, single-process in-memory event bus across agents. Each process has its own event log; cross-process eventing requires Kafka or explicit HTTP calls.
- `src/services/*` are stubs; production CRM/data/notification backends need to be wired.
- The Follow-up Sequences Engine currently implements guardrails internally rather than calling the Compliance Monitor pre-check over HTTP. A future integration can route `POST /compliance-monitor/api/v1/precheck` through the gateway for centralized compliance enforcement.
- Real-time event fan-out to `performance_analytics` and `feedback_loop` is P4 (streaming consumers).
