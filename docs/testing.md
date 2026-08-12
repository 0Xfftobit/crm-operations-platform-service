# Testing Guide

This document describes how to run tests for all five agent engines, the shared API gateway, and the repository-wide integration suite.

## 1. Quick Start

### All engines (from each agent directory)

```bash
for d in src/agents/*/; do
  (cd "$d" && uv run pytest -q)
done
```

### Root gateway / integration suite

```bash
cd /path/to/crm-operations-platform-service
python -m pytest tests -q
```

### Lint and format

```bash
# Root gateway + shared config
ruff check src config tests

# Each engine
for d in src/agents/*/; do
  (cd "$d" && ruff check app tests)
done
```

## 2. Per-Agent Test Commands

### Compliance Monitor

```bash
cd src/agents/compliance_monitor
uv run pytest -q
```

Expected: `179 passed` (includes `tests/test_requirements.py` with 23 acceptance tests + `tests/test_metrics.py`, `tests/test_resilience.py`)

### Feedback Loop

```bash
cd src/agents/feedback_loop
uv run pytest -q
```

Expected: `92 passed` (includes `tests/test_requirements.py` with 20 acceptance tests + `tests/test_metrics.py`, `tests/test_resilience.py`)

### Follow-up Sequences

```bash
cd src/agents/follow_up_sequences
uv run pytest -q
```

Expected: `121 passed` (includes `tests/test_requirements.py` with 19 acceptance tests + `tests/test_metrics.py`, `tests/test_resilience.py`)

### Performance Analytics

```bash
cd src/agents/performance_analytics
uv run pytest -q
```

Expected: `120 passed` (includes `tests/test_requirements.py` with 22 acceptance tests + `tests/test_metrics.py`, `tests/test_resilience.py`)

### Task Automation

```bash
cd src/agents/task_automation
uv run pytest -q
```

Expected: `135 passed, 1 skipped` (includes `tests/test_requirements.py` with 10 acceptance tests + `tests/test_metrics.py`, `tests/test_resilience.py`)

## 3. Gateway / Integration Tests

Location: `tests/integration/test_gateway.py`, `tests/integration/test_gateway_cross_agent.py`

```bash
cd /path/to/crm-operations-platform-service
python -m pytest tests/integration -q
```

Expected: `16 passed`

Coverage:

- `GET /health` (unauthenticated)
- `GET /agents` discovery (unauthenticated)
- `GET /ready` with no upstreams configured returns `degraded`, with healthy upstreams returns `ok`
- Proxy path `/{slug}/{path}` requires `X-API-Key`
- Unknown agent slug returns `404`
- Unconfigured upstream returns `502`
- Default dev key rejected in non-local `ENV`
- `X-Request-ID` generation / propagation on every proxy response
- RLS headers (`X-Tenant`, `X-Team`, `X-Rep`) forwarded to upstreams
- `GET /metrics` returns Prometheus exposition text
- Request metrics (`http_requests_total`) increment through the gateway

## 4. Requirements / Acceptance Tests

Each agent now has a `tests/test_requirements.py` suite that maps the ticket FR/NFR/BR/acceptance criteria to direct API and module tests:

| Agent | Requirement test file | Count | Key areas |
|-------|----------------------|-------|-----------|
| CMS | `src/agents/compliance_monitor/tests/test_requirements.py` | 23 | precheck, consent, audit, fairness, disclosures, PII, erasure, cases, rules, Kafka, security, request-id, dashboard |
| FLS | `src/agents/feedback_loop/tests/test_requirements.py` | 20 | predictions/outcomes/feedback, labels, metrics, drift, retraining, promotion, A/B, Kafka, security, request-id, lineage |
| FUS | `src/agents/follow_up_sequences/tests/test_requirements.py` | 19 | sequence builder, enrollment, guardrails, branching, stop conditions, CMS precheck, providers, Kafka, suppression, owner notification, security |
| PAS | `src/agents/performance_analytics/tests/test_requirements.py` | 22 | ingestion, governed metrics, dashboards, attribution, channels, reconciliation, alerts/reports, export, FLS fan-out, Kafka, security, RLS |
| TAS | `src/agents/task_automation/tests/test_requirements.py` | 10 | pre-approval, idempotency, canary, ledger, LLM write block, JSONLogic, Kafka stub, DLQ replay, requirements matrix |

These are run by the normal `uv run pytest -q` command in each agent directory.

## 5. P4 Feature Tests

### Gateway

The new P4 tests live in `tests/integration/test_gateway.py`. They validate auth, header forwarding, per-agent timeouts, and unknown/upstream error handling.

### Follow-up Sequences

- Redis-backed frequency counters: `app/frequency.py` selects Redis when `CACHE_GATEWAY=redis`; falls back to in-memory.
- CMS precheck client: `app/clients/compliance_client.py`. Add `FUS_PRECHECK_ENABLED=true` and `COMPLIANCE_MONITOR_URL` to exercise.

### Performance Analytics

- FLS outcome fan-out: `app/clients/feedback_client.py`. Add `OUTCOME_FANOUT_ENABLED=true` and `FEEDBACK_LOOP_URL` to exercise.

### Task Automation

- FUS fan-out: `app/clients/followup_client.py`. Add `FUS_FANOUT_ENABLED=true` and `FOLLOW_UP_SEQUENCES_URL` to exercise.

### Kafka / Redpanda

Every agent now has a `app/messaging/kafka_bus.py` (CMS uses `app/kafka_bus.py`) with an in-memory fallback. Broker-free contract tests are in each agent's `tests/test_kafka_bus.py` (TAS uses `tests/test_kafka_contract.py`). To exercise the real Redpanda/Kafka path:

1. Start the local stack: `docker compose --profile infra up -d`
2. In each agent's `.env` set:
   ```bash
   KAFKA_BOOTSTRAP_SERVERS=localhost:9092
   KAFKA_PRODUCE=true
   KAFKA_CONSUMER_ENABLED=true
   ```
3. Run `uv run pytest -q` again; the contract tests will fallback gracefully if the broker is unreachable.

## 6. Observability / Resilience Tests

Each agent and the gateway now expose `GET /api/v1/metrics` (gateway uses `GET /metrics`) and have dedicated test modules:

- `tests/test_metrics.py` — asserts the endpoint returns Prometheus exposition text and that `http_requests_total`, `http_request_duration_seconds`, and per-topic Kafka counters appear.
- `tests/test_resilience.py` — covers `CircuitBreaker` / `ResilientClient` retry, circuit-open fallback, and header propagation.
- `tests/test_health.py` — extended to assert `/ready` dependency checks for `storage`, `postgres`, `redis`, and `kafka`.

Run them with the normal `uv run pytest -q` command.

## 7. Manual Smoke Tests

### Start the local stack

```bash
docker compose --profile infra up --build
```

This starts Postgres, Redis, Redpanda (Kafka), Prometheus, Grafana, the OpenTelemetry Collector, the five agents, and the gateway.

### Gateway health / ready

```bash
curl http://localhost:8080/health
curl http://localhost:8080/ready -H "X-API-Key: change-me-dev-key"
```

### Proxy to an engine

```bash
curl http://localhost:8080/compliance-monitor/api/v1/health \
  -H "X-API-Key: change-me-dev-key"

# Create a sequence through the gateway
curl http://localhost:8080/follow-up-sequences/api/v1/sequences \
  -H "X-API-Key: change-me-dev-key" \
  -H "Content-Type: application/json" \
  -X POST \
  -d '{
    "sequence_id": "sq-1",
    "name": "Welcome",
    "tenant_id": "default",
    "version": 1,
    "steps": [
      {"id": "s1", "channel": "email", "delay": "PT0M", "template": "Hi {{name}}"}
    ]
  }'
```

## 8. CI Notes

A CI job should:

1. Install dependencies:
   - Root: `uv sync --extra dev` (or `pip install -r requirements.txt` for runtime only)
   - Each engine: `uv sync --all-groups` (includes the `dev` group with `pytest` and `ruff`)
2. Run `ruff check` in the root (`uv run --extra dev ruff check src config tests`) and in each engine (`uv run ruff check app tests`).
3. Run `pytest` in the root (`uv run --extra dev pytest -q tests` or `python -m pytest tests`).
4. Run `uv run pytest -q` in `src/agents/{compliance_monitor,feedback_loop,follow_up_sequences,performance_analytics,task_automation}`.

## 9. SQL-Mode Smoke Tests

Each agent with `STORAGE_GATEWAY=postgres` can be validated against SQLite without a live Postgres cluster. These run the same test code through the SQL repository path.

### Follow-up Sequences

```bash
cd src/agents/follow_up_sequences
STORAGE_GATEWAY=postgres DATABASE_URL=sqlite:///:memory: DB_AUTO_CREATE=true \
  uv run pytest -q tests/test_api.py tests/test_pipeline.py
```

Expected: `25 passed`

### Performance Analytics

```bash
cd src/agents/performance_analytics
DATABASE_URL=sqlite:///.test.db STORAGE_GATEWAY=postgres DB_AUTO_CREATE=true \
  uv run pytest -q -k 'not test_readiness_default'
```

Expected: `120 passed, 1 deselected`

### Feedback Loop

```bash
cd src/agents/feedback_loop
DATABASE_URL=sqlite:///.test.db STORAGE_GATEWAY=postgres DB_AUTO_CREATE=true \
  uv run pytest -q -k 'not test_readiness_default'
```

Expected: `91 passed, 1 deselected`

### Alembic migration smoke tests

```bash
for d in follow_up_sequences performance_analytics feedback_loop; do
  (
    cd "src/agents/$d"
    STORAGE_GATEWAY=postgres DATABASE_URL=sqlite:///./.test_alembic.db \
      uv run alembic upgrade head
    rm -f .test_alembic.db
  )
done
```

> **Note:** `test_readiness_default` is deselected in SQL mode because it asserts `postgres` is `not_configured`, which is no longer true when `STORAGE_GATEWAY=postgres`. In production, run `alembic upgrade head` and set `DB_AUTO_CREATE=false`.

The `feedback_loop` engine uses `scikit-learn`; the first import is relatively slow, so allow extra time for cold-start test runs.

---

## 10. Master Test Matrix

This matrix lists every test module in the monorepo, grouped by agent / gateway. Test counts come from `pytest --collect-only -q` against the current code. A standalone copy also lives in `docs/test_matrix.md`.

### Unit and functional test modules

| Agent | Test Module | Tests | Focus |
|-------|-------------|-------|-------|
| **Gateway** | `tests/integration/test_gateway` | 9 | Proxy, auth, request ID, dev-key rejection, ready degradation |
| **Gateway** | `tests/integration/test_gateway_cross_agent` | 5 | Cross-agent header propagation, RLS headers, upstream health |
| **Gateway** | `tests/integration/test_metrics` | 2 | Prometheus `/metrics` exposition |
| **CMS** | `tests/test_activity_dashboard` | 6 | Activity ledger, dashboard alerts |
| **CMS** | `tests/test_api` | 6 | REST API round-trips, precheck, fairness, rule workflow |
| **CMS** | `tests/test_audit` | 5 | Audit hash chain, tamper detection |
| **CMS** | `tests/test_audit_findings` | 7 | Consent cache, disclosure clock, missing explainability |
| **CMS** | `tests/test_coverage_concurrency_batch` | 10 | HTTP polling, coverage sweep, dead-letter alerts, concurrency |
| **CMS** | `tests/test_disclosures` | 4 | ECOA/TCPA disclosure lifecycle |
| **CMS** | `tests/test_fairness` | 4 | Fairness metrics and four-fifths rule |
| **CMS** | `tests/test_gaps` | 15 | Gap detection, coverage alerts, sequence tracking |
| **CMS** | `tests/test_health` | 5 | `/health` and `/ready` probes |
| **CMS** | `tests/test_kafka_bus` | 11 | Kafka producer/consumer contract |
| **CMS** | `tests/test_kafka_spark_cache` | 10 | Spark/batch redlining detection, Redis cache fallback |
| **CMS** | `tests/test_lifecycle_latency` | 8 | Retention, purge, cold tier archive, latency budget |
| **CMS** | `tests/test_metrics` | 2 | Prometheus metrics endpoint |
| **CMS** | `tests/test_new_tiers` | 10 | Stage disclosures, deal tracking, tracing, sequence gaps |
| **CMS** | `tests/test_persistence_sql` | 8 | Postgres/SQLite persistence, ledger tamper, full workflow |
| **CMS** | `tests/test_precheck` | 6 | Consent, opt-out, DNC, quiet hours, CAN-SPAM |
| **CMS** | `tests/test_redis_consent_cache` | 5 | Redis consent cache with in-memory fallback |
| **CMS** | `tests/test_requirements` | 23 | Functional and security requirements |
| **CMS** | `tests/test_resilience` | 2 | Retry and circuit-breaker client |
| **CMS** | `tests/test_rules` | 7 | Rule versioning, two-step approval, deploy history |
| **CMS** | `tests/test_security_config` | 11 | API key and CORS hardening |
| **CMS** | `tests/test_worm_coldstore_local` | 6 | Local WORM mirror and cold store |
| **CMS** | `tests/test_worm_merkle` | 8 | Merkle tree, inclusion proofs, anchor, S3 WORM |
| **FLS** | `tests/test_abtest_and_feedback` | 9 | A/B test allocation, significance, feedback events |
| **FLS** | `tests/test_disk_persistence` | 1 | Disk snapshot/restore of datasets and models |
| **FLS** | `tests/test_feedback_loop` | 25 | Core prediction, label, drift, retrain, promotion workflows |
| **FLS** | `tests/test_health` | 3 | `/health` and `/ready` probes |
| **FLS** | `tests/test_kafka_bus` | 16 | Kafka producer/consumer contract, canonical topics |
| **FLS** | `tests/test_metrics` | 3 | Prometheus metrics endpoint |
| **FLS** | `tests/test_requirements` | 20 | Functional, security, and cross-agent requirements |
| **FLS** | `tests/test_resilience` | 3 | Retry, circuit breaker, mock network failures |
| **FLS** | `tests/test_retraining_rejections_and_triggers` | 8 | Retrain validation, drift/accuracy triggers |
| **FLS** | `tests/test_security` | 4 | Default key rejection, CORS, request ID |
| **FUS** | `tests/test_api` | 7 | REST API: sequence, enrollment, engagement, branching |
| **FUS** | `tests/test_bug_audit` | 5 | Regression tests from bug audit |
| **FUS** | `tests/test_config_hardening` | 4 | Default key, CORS, configuration validation |
| **FUS** | `tests/test_cross_agent` | 1 | CMS precheck fan-out with resilience headers |
| **FUS** | `tests/test_guardrails` | 14 | Consent, DNC, quiet hours, frequency cap, branching |
| **FUS** | `tests/test_health` | 7 | `/health` and `/ready` probes, Postgres check |
| **FUS** | `tests/test_kafka_bus` | 13 | Kafka producer/consumer, cross-agent events |
| **FUS** | `tests/test_metrics` | 2 | Prometheus metrics endpoint |
| **FUS** | `tests/test_p4_gaps` | 19 | P4 feature and gap validation |
| **FUS** | `tests/test_pipeline` | 18 | End-to-end sequence pipeline, scheduler, analytics |
| **FUS** | `tests/test_provider_resilience` | 5 | Provider retry, bounce/complaint, multi-channel |
| **FUS** | `tests/test_redis_frequency_store` | 5 | Redis frequency cap with in-memory fallback |
| **FUS** | `tests/test_requirements` | 19 | Functional and security requirements |
| **FUS** | `tests/test_resilience` | 2 | Retry and circuit-breaker client |
| **PAS** | `tests/test_anomaly_and_reconciliation` | 8 | Anomaly detection, reconciliation windows |
| **PAS** | `tests/test_attribution_advanced` | 6 | Multi-touch attribution, Markov/Shapley paths |
| **PAS** | `tests/test_bug_audit` | 7 | Regression tests from bug audit |
| **PAS** | `tests/test_cross_agent` | 2 | FLS outcome fan-out with resilience headers |
| **PAS** | `tests/test_feedback_client_and_reports` | 11 | Feedback client, CSV/PDF report generation |
| **PAS** | `tests/test_health` | 3 | `/health` and `/ready` probes, Postgres check |
| **PAS** | `tests/test_kafka_bus` | 14 | Kafka producer/consumer contract |
| **PAS** | `tests/test_metrics` | 3 | Prometheus metrics endpoint |
| **PAS** | `tests/test_performance_analytics` | 34 | KPIs, funnel, scorecards, semantic layer |
| **PAS** | `tests/test_requirements` | 22 | Functional, NFR, and security requirements |
| **PAS** | `tests/test_security_and_rls` | 10 | API key, CORS, RLS headers |
| **TAS** | `tests/test_acceptance` | 8 | Acceptance criteria, end-to-end rule firing |
| **TAS** | `tests/test_api` | 3 | REST API endpoints |
| **TAS** | `tests/test_architecture` | 5 | Component and system architecture requirements |
| **TAS** | `tests/test_bug_audit` | 6 | Regression tests from bug audit |
| **TAS** | `tests/test_business_hours` | 3 | Business hours and holiday rules |
| **TAS** | `tests/test_config_security` | 4 | Default key, CORS, env hardening |
| **TAS** | `tests/test_cross_agent` | 3 | FUS follow-up fan-out with resilience headers |
| **TAS** | `tests/test_design` | 6 | System design, schema, workflow design |
| **TAS** | `tests/test_doc_request_workflow` | 3 | Document request workflow and verification |
| **TAS** | `tests/test_functional` | 8 | Functional requirements |
| **TAS** | `tests/test_hardening` | 10 | Security, idempotency, RLS, blast radius |
| **TAS** | `tests/test_health` | 1 | `/health` and `/ready` probes |
| **TAS** | `tests/test_integration_guards` | 24 | Integration guards, trigger handling, event loop |
| **TAS** | `tests/test_kafka_contract` | 7 | Kafka producer/consumer contract |
| **TAS** | `tests/test_llm_client` | 16 | OpenAI-compatible LLM client and fallback |
| **TAS** | `tests/test_metrics` | 3 | Prometheus metrics endpoint |
| **TAS** | `tests/test_persistence` | 4 | Postgres/SQLite persistence, schema init |
| **TAS** | `tests/test_requirements` | 9 | Requirements traceability |
| **TAS** | `tests/test_resilience` | 2 | Retry and circuit-breaker client |
| **TAS** | `tests/test_temporal_activities` | 4 | Temporal activity and worker interfaces |
| **TAS** | `tests/test_vision` | 7 | Vision and scope requirements |

### Per-agent totals

| Agent | Modules | Tests | Status |
|-------|---------|-------|--------|
| Gateway | 3 | 16 | `16 passed, 1 warning` |
| Compliance Monitor (CMS) | 23 | 179 | `179 passed, 1 warning` |
| Feedback Loop (FLS) | 10 | 92 | `92 passed, 11 warnings` |
| Follow-up Sequences (FUS) | 14 | 121 | `121 passed, 1 warning` |
| Performance Analytics (PAS) | 11 | 120 | `120 passed, 1 warning` |
| Task Automation (TAS) | 21 | 135 | `135 passed, 1 skipped, 1 warning` |
| **Grand total** | **82** | **663** | |

### SQL-mode smoke tests

The same test modules can run through the SQL repository path using SQLite:

| Agent | Command | Expected |
|-------|---------|----------|
| FUS | `STORAGE_GATEWAY=postgres DATABASE_URL=sqlite:///:memory: DB_AUTO_CREATE=true uv run pytest -q tests/test_api.py tests/test_pipeline.py` | `25 passed` |
| PAS | `DATABASE_URL=sqlite:///.test.db STORAGE_GATEWAY=postgres DB_AUTO_CREATE=true uv run pytest -q -k 'not test_readiness_default'` | `120 passed, 1 deselected` |
| FLS | `DATABASE_URL=sqlite:///.test.db STORAGE_GATEWAY=postgres DB_AUTO_CREATE=true uv run pytest -q -k 'not test_readiness_default'` | `91 passed, 1 deselected` |
| Alembic | `STORAGE_GATEWAY=postgres DATABASE_URL=sqlite:///./.test_alembic.db uv run alembic upgrade head` | `head` succeeds |

> `test_readiness_default` is deselected in SQL mode because it asserts Postgres is `not_configured`, which is no longer true when `STORAGE_GATEWAY=postgres`.
