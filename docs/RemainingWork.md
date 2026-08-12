# Remaining Implementation & Configuration Checklist

This document consolidates everything that still needs to be implemented or configured before the CRM Operations Platform Service is production-ready.

## 0. Completed in This P4 Pass (2026-08-09)

The following items were implemented or wired during the latest P4 pass. They are no longer blockers for local development, CI, or staged demos.

- [x] **Shared `docker-compose.yml` infrastructure**: added optional `--profile infra` with Postgres, Redis, Redpanda (Kafka), Schema Registry, Prometheus, Grafana, and OpenTelemetry Collector.
- [x] **Gateway P4 hardening**:
  - `X-API-Key` enforcement on proxy routes; 401/404/502/504 handling.
  - Per-agent `COMPLIANCE_MONITOR_TIMEOUT`, `TASK_AUTOMATION_TIMEOUT`, etc.
  - Header forwarding for auth and RLS headers.
  - Rejection of the default `change-me-dev-key` in non-local environments.
- [x] **Cross-agent HTTP clients**:
  - FUS → CMS precheck (`app/clients/compliance_client.py`).
  - PAS → FLS outcome fan-out (`app/clients/feedback_client.py`).
  - TAS → FUS stage-changed / lead-scored fan-out (`app/clients/followup_client.py`).
- [x] **Redis-backed frequency counters in FUS**: `RedisFrequencyStore` with atomic INCR + TTL, activated by `CACHE_GATEWAY=redis`.
- [x] **`.env.example` files** updated across root, FUS, PAS, and TAS.
- [x] **Root integration tests** in `tests/integration/test_gateway.py`.
- [x] **All engine test suites and lint checks pass** — CMS 179, FLS 92, FUS 121, PAS 120, TAS 135 passed (1 skipped), Root 16 passed; see `docs/ENGINE_TESTING_RESULTS.md`.
- [x] **FUS bug-audit fixes**: per-sequence frequency caps, Twilio/SendGrid/WhatsApp Business providers with mock fallback, 5-minute pre-send idempotency guard, `Literal` state validation, `Field(default_factory=dict)` for mutable defaults.
- [x] **TAS bug-audit fixes**: `verify_documents_activity` / `doc_request_chain.complete_verification` default to `verified=False`; `/events/document-received` validates `workflow_id` and rejects non-string ids; `normalize_trigger` asserts and stringifies `trigger_id`; `run_pipeline_stages`, `process_event`, and `_persist_and_emit` assert `trigger_id` is `str`; all TAS in-memory globals (store, runs, ledger, events, DLQ, metrics, guards, escalation, cron, kafka consumers, messaging, integrations, workflows, tracing, LLM agent, `MemoryStore`, `redis_store`) wrapped with `threading.RLock()`.
- [x] **PAS bug-audit fixes**: `reconciliation._funded_amount_for_period` uses `period_utils._in_period` with explicit `now` so ISO-8601 durations (e.g. `P1D`, `P7D`, `P1M`) are evaluated as rolling windows; `_in_period_or_skip` skips missing timestamps; `attribution._time_decay` accepts and uses the actual conversion `funded_at`; `AttributionRepository` uses tuple keys; `build_channel_effectiveness` duplicate branches removed; `list` return-type shadowing fixed with `typing.List`; all in-memory repositories wrapped with `threading.RLock()`.
- [x] **CMS bug-audit fixes**: `coverage_sweep_interval_min`, `http_source.poll_once` non-numeric handling, `CaseUpdate.status`/`service._raise` `Literal`/typed, exact-year retention, `days_left` float, `coldstore.py` `encoding="utf-8"` and S3 range pagination.
- [x] **FLS bug-audit fixes**: `drift.py` `timedelta` import, `model_retrainer.py` `joblib` serialization with in-memory artifact storage.
- [x] **FUS latest P4 hardening**: `SuppressionEvent` + `POST /api/v1/webhooks/suppression`, `Contact.suppressed`/`suppressed_reason`/`owner_id`, `guardrails.check` suppression stops, `service.apply_suppression` with `contact.suppressed` events, `notifications.notify_owner_of_reply` with HTTP-to-in-process-log fallback, `threading.RLock()` in `app/repositories.py`, `X-Request-ID` in `app/clients/compliance_client.py`, and `NOTIFICATION_SERVICE_URL`/`NOTIFICATION_API_KEY`/`INTEGRATION_TIMEOUT_S` in `.env.example` and `app/core/config.py`.
- [x] **Gateway request-id propagation**: `src/api/main.py` generates/propagates `X-Request-ID` and returns it on every proxy response (including 502/404/504); root integration tests `test_gateway_generates_and_returns_request_id` and `test_gateway_propagates_existing_request_id` in `tests/integration/test_gateway.py:70-89`.
- [x] **PAS/TAS cross-agent `X-Request-ID`**: `src/agents/performance_analytics/app/clients/feedback_client.py` and `src/agents/task_automation/app/clients/followup_client.py` send `X-Request-ID`; TAS client deduplicates header building via `_headers()`.
- [x] **Default-dev-key rejection in all agents**: each agent's `Settings` now raises `ValueError` on `change-me-dev-key` when `ENV` is not `local`/`dev`/`development`/`test`, matching the gateway (`src/agents/<agent>/app/core/config.py`).
- [x] **Cross-agent fan-out wired in docker-compose**: `docker-compose.yml` now sets the upstream URLs and enable flags by default for FUS→CMS (`COMPLIANCE_MONITOR_URL`/`FUS_PRECHECK_ENABLED`), PAS→FLS (`FEEDBACK_LOOP_URL`/`OUTCOME_FANOUT_ENABLED`), and TAS→FUS (`FOLLOW_UP_SEQUENCES_URL`/`FUS_FANOUT_ENABLED`).
- [x] **Cross-agent client unit tests**: new `test_cross_agent.py` files in FUS, PAS, and TAS mock `httpx.Client` and verify the clients hit the correct upstream endpoints with `X-API-Key` and `X-Request-ID`.

> **Scope:** All five agent engines (`compliance_monitor`, `feedback_loop`, `follow_up_sequences`, `performance_analytics`, `task_automation`) plus the top-level API gateway.
>
> **Current state (P0–P3 + code-level P4):** All core business logic is implemented and tested. Code-level P4 hardening is done: CORS/dev-key rejection, request-id propagation, cross-agent clients, RLock-protected in-memory state, LLM client, and Kafka contract bus. The repo is ready for local development, staged demos, and CI. It is **not ready for production** without the infrastructure and credentials listed below.

---

## 0.5. Completed in This Session (2026-08-10)

- [x] **Kafka contract bus in all five agents**: producers, consumers, canonical/short topic helpers, per-deal/lead/contact partition keys, and graceful in-memory fallback (`app/messaging/kafka_bus.py`, plus `compliance_monitor/app/kafka_bus.py`).
- [x] **Kafka contract tests aligned to CMS/TAS reference** for FUS, PAS, and FLS; `tests/test_kafka_bus.py` now follows the same 10-pattern structure as the reference files.
- [x] **PAS Kafka producer bug fix**: `_publish_real()` now returns success/failure so `produce()` only marks a row as `transport="kafka"` when the broker send succeeds; on failure it falls back to the in-memory bus.
- [x] **New Kafka config fields** added to all agents: `kafka_consumer_enabled`, `kafka_consumer_group`, `kafka_topics` (CSV/JSON), and `KAFKA_PRODUCE` flags; `.env.example` files updated.
- [x] **Test counts increased**: CMS 179, FLS 92, FUS 121, PAS 120, TAS 135 passed (1 skipped), Root 16 passed; **82 test modules, 663 total unit/functional tests** in `docs/test_matrix.md`.
- [x] **Testing docs refreshed**: `docs/ENGINE_TESTING_RESULTS.md`, `docs/engine_test_results.md`, `docs/testing.md`, and `docs/test_matrix.md` updated with the latest run outputs, the master test matrix, and Kafka/SQL smoke test instructions.

---

## Legend

| Priority | Meaning |
|----------|---------|
| **P0 (now)** | Blocker for any shared/staged environment |
| **P4** | Promotion / GA hardening phase |
| **GA** | Production-launch readiness |

Items are grouped by concern. Each item references the source document(s) that describe it in detail.

---

## 1. Cross-Cutting Platform & Security

These affect every engine and the gateway.

### Authentication & authorization

- [ ] Add a consistent `X-API-Key` / OAuth2 dependency to every write endpoint across all five agents and the gateway.
- [x] Reject the default dev key (`change-me-dev-key`) in non-debug/production mode. **Done:** all agents and gateway now reject `change-me-dev-key` and wildcard `CORS_ORIGINS` in non-local envs.
- [ ] Add RBAC/role scopes for read vs. write vs. export vs. admin actions.
- [x] Make the gateway optionally inject a platform API key for upstream calls. **Done:** gateway passes `X-API-Key` to upstreams and cross-agent clients include it.
- [x] Add auth-aware tests for every agent. **Done:** `test_security_config.py` / `test_config_hardening.py` and gateway `test_gateway.py` cover default-key and wildcard-CORS rejection.

*Sources:* `docs/BUG_AUDIT_REPORT.md` §7.3; `docs/agent_communication.md` §5; all `implementation_status_*` P4 sections.

### Gateway & routing

- [x] Add per-agent proxy integration tests beyond `/health` and `/agents` (404, 502, proxy forwarding, auth pass-through, file downloads). **Done:** `tests/integration/test_gateway.py` plus `tests/integration/test_gateway_cross_agent.py` now have 14 tests covering 502/504/404, auth, header pass-through, request-id, RLS header forwarding, and upstream ready ok/degraded.
- [x] Add end-to-end cross-agent tests (FUS → CMS precheck, TAS → FUS enrollment, PAS → FLS outcomes, etc.). **Done:** per-agent `tests/test_cross_agent.py` files mock the HTTP clients; new per-agent `tests/test_requirements.py` suites also exercise the cross-agent clients. Gateway cross-agent propagation is tested in `tests/integration/test_gateway_cross_agent.py`.
- [x] Make the gateway proxy timeout configurable per agent. **Done:** `settings.agent_timeout(agent)` is wired.
- [x] Forward all upstream response headers (except hop-by-hop) so CSV/PDF exports work. **Done:** `src/api/main.py` forwards all upstream headers except `host`, `content-encoding`, `transfer-encoding`, `connection`.
- [x] Add request/response logging and request-id propagation. **Done:** gateway `src/api/main.py:118-161` generates/propagates `X-Request-ID` and returns it on every proxy response (including 502/504/404); root integration tests verify generation and propagation in `tests/integration/test_gateway.py:70-89`.

*Sources:* `docs/BUG_AUDIT_REPORT.md` §6.1; `docs/DEPLOYMENT_READINESS.md` §Deployment Checklist.

### Shared infrastructure — configure in production

- [ ] **Postgres:** provision a Postgres cluster; set per-agent `DATABASE_URL` to `postgresql+psycopg://...`; run `alembic upgrade head` (or `DB_AUTO_CREATE=true` only in dev).
  - [x] CMS: `STORAGE_GATEWAY=postgres` with Alembic migrations (`app/db/orm.py`, `app/db/sql_repositories.py`).
  - [x] TAS: `STORAGE_BACKEND=postgres` with inline schema creation (`app/persistence/db.py`).
  - [x] FUS: `STORAGE_GATEWAY=postgres` with SQLAlchemy ORM, SQL repositories, and Alembic migrations (`app/db/orm.py`, `app/db/sql_repositories.py`).
  - [x] PAS: `STORAGE_GATEWAY=postgres` with SQLAlchemy ORM, SQL repositories, and Alembic migrations (`app/db/orm.py`, `app/db/sql_repositories.py`).
  - [x] FLS: `STORAGE_GATEWAY=postgres` with SQLAlchemy ORM, SQL repositories, and Alembic migrations (`app/db/orm.py`, `app/db/sql_repositories.py`).
- [ ] **Redis:** provision a production Redis cluster; set per-agent `REDIS_URL`; remove in-memory fallback as the primary store.
- [x] **Kafka:** baseline bus code-complete in all agents — producers, consumers, per-deal partition keys, canonical/short topic helpers, and in-memory fallback. **Remaining:** provision a real Kafka/Redpanda cluster, set `KAFKA_BOOTSTRAP_SERVERS`, enable `KAFKA_PRODUCE`/`KAFKA_CONSUMER_ENABLED` per agent, run end-to-end cross-agent fan-out tests, wire Schema Registry, add SASL/SSL/ACLs, standardize DLQ topics, and expose Kafka lag/throughput metrics.
- [ ] **S3 / Object Lock:** provision a WORM-capable bucket for 7-year audit retention of compliance ledgers, training datasets, and exported reports.
- [ ] **Secrets manager:** move `API_KEY`, `DATABASE_URL`, Redis URL, Kafka bootstrap, and third-party service URLs out of `.env` files and into a vault.

*Sources:* `docs/BUG_AUDIT_REPORT.md` §7.3; `docs/DEPLOYMENT_READINESS.md` §Immediate Blockers; all `implementation_status_*` P4 sections.

### Observability

- [x] **Prometheus metrics endpoints:** expose `/api/v1/metrics` from every agent and `/metrics` from the gateway; in-memory fallback when `prometheus-client` is not installed.
- [ ] **Grafana dashboards:** create dashboards for latency, throughput, error rate, and NFR/SLO compliance.
- [ ] **OpenTelemetry distributed tracing:**
  - [x] CMS has an OTel stub with graceful degradation (`app/core/tracing.py`).
  - [ ] FUS and TAS have config fields (`TRACING_ENABLED`/`OTEL_ENABLED`) but need full instrumentation.
  - [ ] PAS and FLS need OTel config and instrumentation.
  - [ ] Propagate trace context across cross-agent HTTP calls and Kafka messages.
- [ ] **Structured error budgets and alerting:** define SLOs, recording rules, burn-rate alerts, and Alertmanager routing.
- [x] **Kafka health in probes:** `storage`, `postgres`, `redis`, and `kafka` checks now reported by `/ready` endpoints.
- [x] **Request-ID log context:** `RequestIDMiddleware` in every agent and the gateway binds `request_id` to `structlog.contextvars` and echoes `X-Request-ID` on responses.

*Sources:* `docs/DEPLOYMENT_READINESS.md` §Deployment Checklist; `docs/BUG_AUDIT_REPORT.md` §7.3.

### HTTP client resilience

- [x] FUS channel providers retry with exponential backoff on `429`/transient failures (`app/providers.py:168-190`).
- [x] TAS Temporal activities have a configurable retry/backoff policy (`app/integrations/activity_runner.py:41-129`).
- [x] Add retry to cross-agent HTTP clients (FUS→CMS, PAS→FLS, TAS→FUS) — `ResilientClient` retries 5xx/timeout/network errors up to 3 attempts with exponential backoff.
- [x] Add circuit-breaker pattern to cross-agent HTTP clients — `CircuitBreaker` opens after 3 consecutive failures, stays open 30s, then half-open.
- [ ] Add bulkhead/isolation between different upstream services.

### CORS, security & packaging

- [ ] Restrict `CORS_ORIGINS` from `["*"]` to the production web domain(s) in every agent.
- [ ] Run containers as non-root users.
- [ ] Enable TLS termination and network policies.
- [ ] Consolidate or remove top-level `requirements.txt` duplication.
- [ ] Pin and scan base images; add `HEALTHCHECK` to Dockerfiles.

*Sources:* `docs/BUG_AUDIT_REPORT.md`; `docs/DEPLOYMENT_READINESS.md` §Production.

---

## 2. Compliance Monitor System (CMS)

### Features still to implement

- [x] Kafka bus baseline (producers, consumers, per-deal partition keys, in-memory fallback) — production broker and end-to-end fan-out still pending.
- [ ] Real-time fair-lending batch analytics: pure aggregation exists (`app/spark_batch.py`, `POST /fairness/batch`), but Spark/Flink cluster integration and warehouse SQL are pending.
- [ ] Automated external regulatory feed monitoring (`app/regwatch.py`).
- [ ] Full BI dashboard UI beyond the JSON `GET /dashboard`.
- [ ] External TCPA DNC service integration.
- [ ] S3 WORM/Object Lock cold storage automation (local WORM/cold-store backends implemented; S3 not wired).
- [ ] Engine-8 central correlation with signed policy verification.

*Sources:* `docs/implementation_status_cms.md` §4–§8.

### Code-quality / bug-audit items

- [x] Fix coverage sweep interval in `app/main.py` (now uses `coverage_sweep_interval_min`; `app/core/config.py` adds the field).
- [x] Harden `app/http_source.py` against non-numeric `source_seq` and cursor.
- [x] Tighten `CaseUpdate.status` to `Literal["open", "investigating", "remediated", "closed"]` and `service._raise` to `Domain`/`Severity` types.
- [x] Add locking around global in-memory stores (`audit.py`, `cache.py`, `coverage.py`, `events.py`, `kafka_bus.py`, `worm.py`, `coldstore.py`, `casework.py` dedup, and in-memory `app/repositories.py`).
- [x] Use exact-year retention with leap-year handling and float `days_left` from total seconds for disclosure/retention timers.
- [x] Use explicit `encoding="utf-8"` in `app/coldstore.py`.
- [x] Combine S3 cold-store pagination passes.

*Sources:* `docs/BUG_AUDIT_REPORT.md` §1.

### Configuration to set

- [ ] `CORS_ORIGINS` — restrict to production origin(s).
- [ ] `DATABASE_URL` — Postgres for consent/audit/rules persistence.
- [ ] `REDIS_URL` — production Redis endpoint for consent cache and hot counters.
- [ ] `KAFKA_BOOTSTRAP_SERVERS` and `KAFKA_PRODUCE=true` / `KAFKA_INGEST=true`.
- [ ] `WORM_GATEWAY` / `COLD_STORE_GATEWAY` and S3 credentials.
- [ ] `AUDIT_RETENTION_YEARS`, `chain_verify_interval_min`, `coverage_heartbeat_max_min`.

---

## 3. Feedback Loop System (FLS)

### Features still to implement

- [ ] Integrate real MLflow model registry and artifact store.
- [ ] Add feature store (Feast/Tecton) for point-in-time feature snapshots.
- [x] Kafka consumer baseline for model retrain/outcome events — production broker and MLflow integration still pending.
- [ ] Production S3/DW/MLflow integration for model artifacts, training datasets, and feature store.
- [ ] Shadow + canary deployment with gradual ramp and auto-rollback (model status field reserves `shadow`/`canary`, no ramp logic).
- [ ] Propensity / exploration weighting for feedback bias mitigation.
- [ ] Right-to-erasure propagation into training datasets.
- [ ] Auto-generated model cards per promoted model.
- [ ] Production scheduler for monthly/scheduled retrains.
- [ ] External alerting (Slack/email/PagerDuty) on drift/retrain events.

*Sources:* `docs/implementation_status_fls.md` §4–§8.

### Code-quality / bug-audit items

- [x] Fix A/B traffic split rounding so variants sum to exactly 100% (`app/abtest.py:72-82`).
- [x] Fix A/B guardrail fallback metric comparison (`app/abtest.py:230-239`).
- [x] Use exact `timedelta` comparison in drift window (`app/drift.py:86-91`).
- [x] Replace `pickle` model/dataset serialization with `joblib` (`app/model_retrainer.py:216-218,271`).
- [x] Fix `_precision_recall_f1` return type annotation (`app/aggregator.py:50-66`).
- [x] Replace MD5 variant hash with SHA-256 (`app/abtest.py:20-21`).
- [x] Add locks around global in-memory repositories and drift reference state (`app/repositories.py`, `app/drift.py`).

*Sources:* `docs/BUG_AUDIT_REPORT.md` §2.

### Configuration to set

- [ ] `API_KEY` — replace default key.
- [ ] `STORAGE_GATEWAY` (default `memory`; set `postgres` + `DATABASE_URL` for production), `STORAGE_BACKEND`, `DATA_DIR`, and S3/MLflow for production.
- [ ] `REDIS_URL` for shared state.
- [ ] `KAFKA_BOOTSTRAP_SERVERS` and `KAFKA_INGEST=true`.
- [ ] `MLFLOW_TRACKING_URI` and `MLFLOW_API_KEY`.
- [ ] `MIN_TRAINING_SAMPLES`, `ACCURACY_THRESHOLD`, `ACCURACY_EPSILON`, `FAIRNESS_RATIO_MIN/MAX`.

---

## 4. Follow-up Sequences Engine (FUS)

### Features still to implement

- [x] Real Twilio/SendGrid/WhatsApp providers with mock fallback and single-provider retry.
- [ ] Multi-provider failover (currently one provider per channel with retry only).
- [ ] Temporal durable per-contact workflows (code exists, `TEMPORAL_ENABLED=false` by default).
- [x] Kafka consumer baseline for `lead.scored` / `deal.stage_changed` / `contact.replied` — production broker still pending.
- [x] Redis shared frequency-cap counters (`RedisFrequencyStore` optional via `CACHE_GATEWAY=redis`); consent cache P4.
- [x] Postgres durable persistence for definitions, contacts, enrollments, ledger, and engagement events (`STORAGE_GATEWAY=postgres` with SQLAlchemy ORM + Alembic).
- [ ] Engine 3 generative content integration for templates.
- [ ] Timing Optimizing Engine best-time window consumption.
- [ ] Per-variant A/B conversion reporting.
- [x] Bounce/complaint suppression list ingestion. **Done:** `SuppressionEvent` + `POST /api/v1/webhooks/suppression` (`app/api/sequences.py:124-126`); `service.apply_suppression` (`app/service.py:365-383`); in-process per-tenant list.
- [x] LO/owner notifications on reply. **Done:** `Contact.owner_id` + `app/notifications.py:notify_owner_of_reply` with HTTP-to-in-process-log fallback (`app/notifications.py:46-73`); `service.ingest_engagement` invokes it when `contact.owner_id` is set (`app/service.py:300-305`).
- [ ] Load test ≥ 2,000 messages/s.

*Sources:* `docs/implementation_status_fus.md` §4, §6.

### Code-quality / bug-audit items

- [x] Timezone-normalize frequency-cap day/week keys to UTC.
- [x] Validate `StateRequest.state` as `Literal`.
- [x] Use `Field(default_factory=dict)` for mutable Pydantic defaults.
- [x] Add locks around global in-memory state. **Done:** `app/repositories.py` uses per-instance `threading.RLock()` via `_synchronized` (`app/repositories.py:23-29`); `app/events.py`, `app/notifications.py`, `app/providers.py`, and `app/frequency.FrequencyStore` now use module-level `threading.RLock()` to protect their in-process logs, provider registry, mock send log, and frequency counters (`app/events.py:31-59`, `app/notifications.py:19-82`, `app/providers.py:25-175`, `app/frequency.py:39-91,187-204`).
- [x] Add idempotent pre-send dedupe guard against redelivery.

*Sources:* `docs/BUG_AUDIT_REPORT.md` §3; `docs/implementation_status_fus.md` §3.

### Configuration to set

- [ ] `CORS_ORIGINS`, `STORAGE_GATEWAY`/`DATABASE_URL`, `REDIS_URL`.
- [ ] `KAFKA_BOOTSTRAP_SERVERS` and `KAFKA_PRODUCE=true`.
- [ ] `TWILIO_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, `SENDGRID_API_KEY`, `WHATSAPP_*`.
- [ ] `COMPLIANCE_MONITOR_URL` and `TIMING_OPTIMIZER_URL` for cross-agent calls.
- [ ] `LEAD_SCORE_THRESHOLD`, `DEFAULT_TIMEZONE`, `FREQUENCY_CAP_DAY`, `FREQUENCY_CAP_WEEK`.

---

## 5. Performance Analytics System (PAS)

### Features still to implement

- [x] Postgres SQL persistence implemented (`STORAGE_GATEWAY=postgres` with SQLAlchemy ORM + Alembic). Remaining: materialized KPI refresh and DW/S3 integration.
- [x] Kafka producer/consumer baseline for streaming touches and outcomes — real streaming pipeline/DW/Flink still pending.
- [ ] Row-level security with SSO/policy engine.
- [ ] Scheduled email delivery of reports (schedule entity exists, no cron/email mechanism).
- [ ] Forecast-attainment vs. Forecasting Engine integration.
- [ ] Self-serve exploration UI and metric lineage graph.
- [ ] Right-to-erasure / pseudonymization audit.

*Sources:* `docs/implementation_status_pas.md` §4, §8.

### Code-quality / bug-audit items

- [x] Fix `_period_grain` to parse ISO durations vs. bucket keywords correctly.
- [x] Fix `datetime.min` fallback so deals without valid timestamps do not pollute every period.
- [x] Fix time-decay attribution `funded_at` reference.
- [x] Fix `AttributionRepository` key type annotation.
- [x] Simplify duplicate branches in `build_channel_effectiveness`.
- [x] Add locks around global in-memory repositories. **Done:** PAS repositories and service state now use `threading.RLock()`.

*Sources:* `docs/BUG_AUDIT_REPORT.md` §4.

### Configuration to set

- [ ] `CORS_ORIGINS`, `STORAGE_GATEWAY`/`DATABASE_URL`, `REDIS_URL`.
- [ ] `KAFKA_BOOTSTRAP_SERVERS` and `KAFKA_INGEST=true`.
- [ ] `DW_ACCOUNT`, `DW_USER`, `DW_PASSWORD`, `DW_WAREHOUSE`.
- [ ] `ATTRIBUTION_LOOKBACK_D`, `ATTRIBUTION_DEFAULT_MODEL`, `TIME_DECAY_HALFLIFE_D`, `RECON_TOLERANCE`.

---

## 6. Task Automation Engine (TAS)

### Features still to implement

- [x] Kafka producer/consumer with partition-keyed per-deal ordering — real broker still pending.
- [ ] Real Temporal cluster deployment and worker pools (Temporal client code exists, `TEMPORAL_ENABLED=false` by default).
- [ ] Production persistence: Postgres implemented (`STORAGE_BACKEND=postgres`), Redis idempotency optional (`IDEMPOTENCY_BACKEND=redis`), S3 exports still pending.
- [x] HTTP fallback CRM service client (`app/integrations/crm.py` calls `CRM_SERVICE_URL` when configured).
- [x] HTTP fallback calendar client (`app/integrations/calendar.py` calls `CALENDAR_SERVICE_URL` when configured); Google/Outlook/Exchange OAuth P4.
- [x] OpenAI-compatible LLM client with deterministic fallback (`app/integrations/llm_client.py`); production model governance / provider routing P4.
- [ ] Load test to 15k events/s (p95 ≤ 3s).
- [ ] DLQ replay UI / admin tooling (replay API exists; UI missing).
- [ ] Shadow/canary analytics dashboards.
- [ ] Full tenant/branch SSO/policy enforcement.

*Sources:* `docs/implementation_status_tas.md` §4, §7.

### Code-quality / bug-audit items

- [x] Fix doc-request workflow verification to default to `False`.
- [x] Tighten `wf_id` and `trigger_id` types at API call sites (`/events/document-received` validates the resolved `workflow_id`).
- [ ] Add locks or Redis backing for in-memory idempotency/loop-budget stores.
- [ ] Wire `app/persistence/redis_store.py` into idempotency and rule cache paths.

*Sources:* `docs/BUG_AUDIT_REPORT.md` §5; `docs/implementation_status_tas.md` §8.

### Configuration to set

- [ ] `CORS_ORIGINS`, `STORAGE_BACKEND`/`DATABASE_URL`, `REDIS_URL`.
- [ ] `KAFKA_BOOTSTRAP_SERVERS` and `KAFKA_CONSUMER_ENABLED=true`.
- [ ] `TEMPORAL_HOST`, `TEMPORAL_NAMESPACE`, mTLS certs.
- [ ] `CRM_SERVICE_URL`, `CRM_API_KEY`.
- [ ] `GOOGLE_CLIENT_*`, `MICROSOFT_CLIENT_*` for calendar.
- [ ] `LLM_PROVIDER_URL`, `LLM_API_KEY`, `LLM_MODEL`.

---

## 7. Integration / Cross-Agent Orchestration

- [x] Wire cross-agent HTTP clients:
  - FUS → CMS: `POST /api/v1/precheck` before any send.
  - PAS → FLS: fan out `deal.funded` / `deal.lost` outcomes.
  - TAS → FUS: emit `deal.stage_changed` and `lead.assigned` events.
- [ ] Add retry, circuit-breaker, and timeout policies on all cross-agent and third-party HTTP calls.
- [x] Add real Kafka consumers/producers for event-driven flows (TAS consumes `crm.deal.stage_changed`, `crm.lead.assigned`, `crm.document.received`; FUS consumes `crm.deal.stage_changed`, `crm.lead.scored`; PAS consumes `crm.outreach.*`, `crm.outcome.*`; FLS consumes `crm.outcome.*`, `crm.outreach.converted`; CMS produces `compliance.*` and consumes `compliance-audit`/`crm.*`).
- [ ] Validate end-to-end cross-agent fan-out with a live broker.
- [x] Add shared compose services for Postgres, Redis, Kafka/Redpanda, Schema Registry, Prometheus, Grafana, and OpenTelemetry Collector (`docker-compose.yml --profile infra`).

### Kafka production hardening

- [ ] Run brokered integration tests in CI using `docker compose --profile infra`.
- [ ] Validate per-deal ordering and end-to-end cross-agent fan-out.
- [ ] Register and validate schemas via the Schema Registry.
- [ ] Add SASL/SSL security config and topic ACLs.
- [ ] Standardize DLQ topics, retry policies, and replay semantics.
- [ ] Add manual offset-commit / exactly-once semantics.
- [ ] Expose Prometheus metrics for Kafka consumer lag and producer throughput.

*Sources:* `docs/agent_communication.md`; `docs/DEPLOYMENT_READINESS.md` §Deployment Checklist.

---

## 8. Deployment & Operations

### Staging environment

- [ ] Provision Postgres for all agents and run `alembic upgrade head` per agent; set `STORAGE_GATEWAY=postgres` and `DATABASE_URL`.
- [ ] Provision Redis and configure all agents with real `REDIS_URL`.
- [ ] Provision Kafka (broker + KRaft) and enable producers/consumers.
- [ ] Add auth middleware on every agent and the gateway.
- [ ] Add `/ready` probes that verify database, Redis, Kafka, Temporal, and upstream dependency health.
- [ ] Add Prometheus/Grafana metrics and OpenTelemetry tracing.

### Production environment

- [ ] Provision S3 WORM bucket and wire 7-year audit archival.
- [ ] Run load tests verifying all NFRs.
- [ ] Add circuit breakers and retries on all third-party HTTP calls.
- [ ] Run containers as non-root.
- [ ] Restrict CORS origins.
- [ ] Enable TLS termination and network policies.
- [ ] Add/verify automated DB migrations (Alembic) for every agent that uses Postgres; CMS has migrations, TAS uses inline schema, FUS/PAS/FLS need migrations built.
- [ ] Write SOC 2 evidence runbooks.

*Sources:* `docs/DEPLOYMENT_READINESS.md` §Deployment Checklist, §Immediate Blockers; `docs/BUG_AUDIT_REPORT.md` §7.3.

---

## 9. Testing & Quality

- [x] Master test matrix created in `docs/test_matrix.md` and copied to `docs/testing.md`, `docs/ENGINE_TESTING_RESULTS.md`, and `docs/engine_test_results.md` (82 modules, 663 unit/functional tests across all agents and the gateway).
- [x] Add gateway proxy tests for every agent (404, upstream failure, auth pass-through, file downloads) — `tests/integration/test_gateway.py` covers 502/504/404, auth, header pass-through, and request-id.
- [ ] Add end-to-end cross-agent tests (TAS → FUS, PAS → FLS, FUS → CMS) in a containerized environment.
- [ ] Add `locust` or `k6` load tests for all five agents (15k/s platform, 2k msg/s FUS).
- [x] Add contract tests for cross-agent HTTP calls and Kafka event schemas.
- [ ] Add live-broker Kafka integration tests using `docker compose --profile infra`.
- [ ] Add nightly drift/label tests in CI for FLS.
- [ ] Keep `ruff`, `mypy`, and pytest passing as the codebase expands.

---

## 10. Quick-Reference: Environment Variables to Configure in Production

| Variable | Used by | Purpose |
|----------|---------|---------|
| `API_KEY` | All agents + gateway | Replace default `change-me-dev-key` with a vault-managed key. |
| `CORS_ORIGINS` | All agents | Restrict to the production web origin. |
| `STORAGE_GATEWAY` / `STORAGE_BACKEND` | All agents | `memory` (default), `postgres` (all agents), `sqlite` (TAS), `disk` (FLS). |
| `DATABASE_URL` | All agents | Postgres URL with migrations. |
| `REDIS_URL` | All agents | Production Redis endpoint; disables in-memory fallback. |
| `KAFKA_BOOTSTRAP_SERVERS` | All agents | Real Kafka/Redpanda broker. |
| `KAFKA_PRODUCE` / `KAFKA_CONSUMER_ENABLED` / `KAFKA_INGEST` | All agents | Toggle real Kafka vs. in-process fallback. |
| `COMPLIANCE_MONITOR_URL` | Gateway, FUS | Upstream CMS endpoint. |
| `TASK_AUTOMATION_URL` | Gateway, FUS | Upstream TAS endpoint. |
| `FOLLOW_UP_SEQUENCES_URL` | Gateway, TAS | Upstream FUS endpoint. |
| `FEEDBACK_LOOP_URL` | Gateway, PAS | Upstream FLS endpoint. |
| `PERFORMANCE_ANALYTICS_URL` | Gateway, FLS | Upstream PAS endpoint. |
| `WORM_GATEWAY` / `COLD_STORE_GATEWAY` | CMS | S3 vs. local WORM and cold storage. |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` | CMS, FLS, PAS | S3 credentials or IRSA role. |
| `MLFLOW_TRACKING_URI` / `MLFLOW_API_KEY` | FLS | Real model registry. |
| `TWILIO_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_PHONE_NUMBER` | FUS | SMS/voice provider. |
| `SENDGRID_API_KEY` | FUS, PAS | Email provider. |
| `WHATSAPP_*` | FUS | WhatsApp Business API credentials. |
| `CRM_SERVICE_URL` / `CRM_API_KEY` | TAS | Real CRM service. |
| `CALENDAR_SERVICE_URL` / `CALENDAR_API_KEY` | TAS | Calendar API. |
| `FEATURE_STORE_URL` / `FEATURE_STORE_API_KEY` | TAS | Feature store. |
| `GOOGLE_CLIENT_*` / `MICROSOFT_CLIENT_*` | TAS | Calendar OAuth. |
| `LLM_PROVIDER_URL` / `LLM_API_KEY` / `LLM_MODEL` | TAS | OpenAI-compatible LLM provider. |
| `TEMPORAL_HOST` / `TEMPORAL_NAMESPACE` | TAS, FUS | Temporal cluster. |
| `TRACING_ENABLED` / `OTEL_ENABLED` / `OTEL_EXPORTER_OTLP_ENDPOINT` | All | OpenTelemetry tracing. |

---

## How to use this checklist

1. **For local dev/CI:** The repository already passes all tests; use this list to explain what is intentionally out of scope for P0–P3.
2. **For staging:** Complete the "Cross-Cutting" and per-agent P4 sections marked with `[ ]` before demoing to business stakeholders.
3. **For production:** Complete *all* items in this document and produce evidence runbooks.

---

## Source documents

- `docs/DEPLOYMENT_READINESS.md`
- `docs/BUG_AUDIT_REPORT.md`
- `docs/agent_communication.md`
- `docs/implementation_status_cms.md`
- `docs/implementation_status_fls.md`
- `docs/implementation_status_fus.md`
- `docs/implementation_status_pas.md`
- `docs/implementation_status_tas.md`
- `docs/ticket_description_cms.md`
- `docs/ticket_description_fls.md`
- `docs/ticket_description_fus.md`
- `docs/ticket_description_pas.md`
- `docs/ticket_description_tas.md`
