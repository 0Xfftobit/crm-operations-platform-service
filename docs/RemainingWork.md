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
- [x] **All engine test suites and lint checks pass** (see `docs/ENGINE_TESTING_RESULTS.md`).
- [x] **FUS bug-audit fixes**: per-sequence frequency caps, Twilio/SendGrid/WhatsApp Business providers with mock fallback, 5-minute pre-send idempotency guard, `Literal` state validation, `Field(default_factory=dict)` for mutable defaults.
- [x] **TAS bug-audit fixes**: HTTP fallback CRM/calendar/feature-store clients, `verify_documents_activity` default `False`, `/events/document-received` workflow-id validation.
- [x] **PAS bug-audit fixes**: `_period_grain` regex, `_in_period_or_skip`, `attribution._time_decay` `funded_at` fix, `AttributionRepository` key typing, duplicate branch removal in `build_channel_effectiveness`.
- [x] **CMS bug-audit fixes**: `coverage_sweep_interval_min`, `http_source.poll_once` non-numeric handling, `CaseUpdate.status`/`service._raise` `Literal`/typed, exact-year retention, `days_left` float, `coldstore.py` `encoding="utf-8"` and S3 range pagination.
- [x] **FLS bug-audit fixes**: `drift.py` `timedelta` import, `model_retrainer.py` `joblib` serialization with in-memory artifact storage.

> **Scope:** All five agent engines (`compliance_monitor`, `feedback_loop`, `follow_up_sequences`, `performance_analytics`, `task_automation`) plus the top-level API gateway.
>
> **Current state (P0–P3):** All core business logic is implemented and tested. In-memory stores, in-process event buses, and stub integrations are used for local development and CI. The repo is ready for local development, integration testing, staged demos, and CI. It is **not ready for production** without the items below.

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
- [ ] Reject the default dev key (`change-me-dev-key`) in non-debug/production mode.
- [ ] Add RBAC/role scopes for read vs. write vs. export vs. admin actions.
- [ ] Make the gateway optionally inject a platform API key for upstream calls.
- [ ] Add auth-aware tests for every agent.

*Sources:* `docs/BUG_AUDIT_REPORT.md` §7.3; `docs/agent_communication.md` §5; all `implementation_status_*` P4 sections.

### Gateway & routing

- [ ] Add per-agent proxy integration tests beyond `/health` and `/agents` (404, 502, proxy forwarding, auth pass-through, file downloads).
- [ ] Add end-to-end cross-agent tests (FUS → CMS precheck, TAS → FUS enrollment, PAS → FLS outcomes, etc.).
- [ ] Make the gateway proxy timeout configurable per agent.
- [ ] Forward all upstream response headers (except hop-by-hop) so CSV/PDF exports work.
- [ ] Add request/response logging and request-id propagation.

*Sources:* `docs/BUG_AUDIT_REPORT.md` §6.1; `docs/DEPLOYMENT_READINESS.md` §Deployment Checklist.

### Shared infrastructure — configure in production

- [ ] **Postgres:** provision a Postgres cluster; set per-agent `DATABASE_URL` to `postgresql+asyncpg://...` or `postgresql+psycopg://...`; add Alembic migrations for agents that need durable persistence.
- [ ] **Redis:** provision a production Redis cluster; set per-agent `REDIS_URL`; remove in-memory fallback as the primary store.
- [ ] **Kafka:** provision a Kafka cluster and Schema Registry; set `KAFKA_BOOTSTRAP_SERVERS` and topic ACLs; add `aiokafka` / `kafka-python` consumer groups for cross-agent event-driven flows.
- [ ] **S3 / Object Lock:** provision a WORM-capable bucket for 7-year audit retention of compliance ledgers, training datasets, and exported reports.
- [ ] **Secrets manager:** move `API_KEY`, `DATABASE_URL`, Redis URL, Kafka bootstrap, and third-party service URLs out of `.env` files and into a vault.

*Sources:* `docs/BUG_AUDIT_REPORT.md` §7.3; `docs/DEPLOYMENT_READINESS.md` §Immediate Blockers; all `implementation_status_*` P4 sections.

### Observability

- [ ] Add Prometheus metrics endpoints and export SLOs per agent.
- [ ] Add Grafana dashboards for latency, throughput, error rate, and NFR compliance.
- [ ] Add OpenTelemetry distributed tracing (CMS has an OTel stub; wire it in all agents).
- [ ] Add structured error budgets and alerting.

*Sources:* `docs/DEPLOYMENT_READINESS.md` §Deployment Checklist; `docs/BUG_AUDIT_REPORT.md` §7.3.

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

- [ ] Production Kafka consumer groups for activity/alert ingestion and `compliance.signal` emission.
- [ ] Real-time fair-lending batch analytics (Spark/Flink or warehouse SQL) and redlining detection.
- [ ] Automated external regulatory feed monitoring (`app/regwatch.py`).
- [ ] Full BI dashboard rendering beyond the JSON `GET /dashboard`.
- [ ] External consent / DNC service integration.
- [ ] S3 WORM/Object Lock cold storage automation.
- [ ] Engine-8 central correlation with signed policy verification.

*Sources:* `docs/implementation_status_cms.md` §4–§8.

### Code-quality / bug-audit items

- [x] Fix coverage sweep interval in `app/main.py` (now uses `coverage_sweep_interval_min`; `app/core/config.py` adds the field).
- [x] Harden `app/http_source.py` against non-numeric `source_seq` and cursor.
- [x] Tighten `CaseUpdate.status` to `Literal["open", "investigating", "remediated", "closed"]` and `service._raise` to `Domain`/`Severity` types.
- [ ] Add locking around global in-memory stores (`audit.py`, `cache.py`, `coverage.py`, `events.py`, `kafka_bus.py`, `worm.py`, `coldstore.py`).
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
- [ ] Add Kafka consumer for prediction/outcome/override events.
- [ ] Production persistence (Postgres + S3/DW) for predictions, labels, and datasets.
- [ ] Shadow + canary deployment with gradual ramp and auto-rollback.
- [ ] Propensity / exploration weighting for feedback bias mitigation.
- [ ] Right-to-erasure propagation into training datasets.
- [ ] Auto-generated model cards per promoted model.
- [ ] Production scheduler for monthly/scheduled retrains.
- [ ] External alerting (Slack/email/PagerDuty) on drift/retrain events.

*Sources:* `docs/implementation_status_fls.md` §4–§8.

### Code-quality / bug-audit items

- [ ] Fix A/B traffic split rounding so variants sum to exactly 100%.
- [ ] Fix A/B guardrail fallback metric comparison.
- [x] Use exact `timedelta` comparison in drift window.
- [x] Replace `pickle` model/dataset serialization with `joblib`.
- [ ] Fix `_precision_recall_f1` return type annotation.
- [ ] Replace MD5 variant hash with SHA-256.
- [ ] Add locks around global in-memory repositories.

*Sources:* `docs/BUG_AUDIT_REPORT.md` §2.

### Configuration to set

- [ ] `API_KEY` — replace default key.
- [ ] `STORAGE_BACKEND` and `DATA_DIR` (or `DATABASE_URL` + S3 for production).
- [ ] `REDIS_URL` for shared state.
- [ ] `KAFKA_BOOTSTRAP_SERVERS` and `KAFKA_INGEST=true`.
- [ ] `MLFLOW_TRACKING_URI` and `MLFLOW_API_KEY`.
- [ ] `MIN_TRAINING_SAMPLES`, `ACCURACY_THRESHOLD`, `ACCURACY_EPSILON`, `FAIRNESS_RATIO_MIN/MAX`.

---

## 4. Follow-up Sequences Engine (FUS)

### Features still to implement

- [x] Real Twilio/SendGrid/WhatsApp providers with mock fallback.
- [ ] Add Temporal durable per-contact workflows with timers and signals.
- [ ] Add Kafka consumer for `lead.scored` / `deal.stage_changed` / `contact.replied`.
- [x] Redis shared frequency-cap counters (`RedisFrequencyStore` optional via `CACHE_GATEWAY=redis`); consent cache P4.
- [ ] Postgres durable persistence for definitions, enrollments, and ledger.
- [ ] Engine 3 generative content integration for templates.
- [ ] Timing Optimizing Engine best-time window consumption.
- [ ] Per-variant A/B conversion reporting.
- [ ] Bounce/complaint suppression list ingestion.
- [ ] LO/owner notifications on reply.
- [ ] Load test ≥ 2,000 messages/s.

*Sources:* `docs/implementation_status_fus.md` §4, §6.

### Code-quality / bug-audit items

- [x] Timezone-normalize frequency-cap day/week keys to UTC.
- [x] Validate `StateRequest.state` as `Literal`.
- [x] Use `Field(default_factory=dict)` for mutable Pydantic defaults.
- [ ] Add locks around global in-memory state (`events.py`, `providers.py`, `repositories.py`).
- [x] Add idempotent pre-send dedupe guard against redelivery.

*Sources:* `docs/BUG_AUDIT_REPORT.md` §3; `docs/implementation_status_fus.md` §3.

### Configuration to set

- [ ] `CORS_ORIGINS`, `DATABASE_URL`, `REDIS_URL`.
- [ ] `KAFKA_BOOTSTRAP_SERVERS` and `KAFKA_PRODUCE=true`.
- [ ] `TWILIO_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, `SENDGRID_API_KEY`, `WHATSAPP_*`.
- [ ] `COMPLIANCE_MONITOR_URL` and `TIMING_OPTIMIZER_URL` for cross-agent calls.
- [ ] `LEAD_SCORE_THRESHOLD`, `DEFAULT_TIMEZONE`, `FREQUENCY_CAP_DAY`, `FREQUENCY_CAP_WEEK`.

---

## 5. Performance Analytics System (PAS)

### Features still to implement

- [ ] Production data warehouse / Postgres persistence and materialized KPI refresh.
- [ ] Near-real-time streaming KPIs (Flink/Kafka).
- [ ] Row-level security with SSO/policy engine.
- [ ] Scheduled email delivery of reports.
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
- [ ] Add locks around global in-memory repositories.

*Sources:* `docs/BUG_AUDIT_REPORT.md` §4.

### Configuration to set

- [ ] `CORS_ORIGINS`, `DATABASE_URL`, `REDIS_URL`.
- [ ] `KAFKA_BOOTSTRAP_SERVERS` and `KAFKA_INGEST=true`.
- [ ] `DW_ACCOUNT`, `DW_USER`, `DW_PASSWORD`, `DW_WAREHOUSE`.
- [ ] `ATTRIBUTION_LOOKBACK_D`, `ATTRIBUTION_DEFAULT_MODEL`, `TIME_DECAY_HALFLIFE_D`, `RECON_TOLERANCE`.

---

## 6. Task Automation Engine (TAS)

### Features still to implement

- [ ] Production Kafka consumer group with partition-keyed per-deal ordering.
- [ ] Real Temporal cluster deployment and worker pools.
- [ ] Production persistence (Postgres + Redis + S3 exports).
- [x] HTTP fallback CRM service client (`app/integrations/crm.py` calls `CRM_SERVICE_URL` when configured).
- [x] HTTP fallback calendar client (`app/integrations/calendar.py` calls `CALENDAR_SERVICE_URL` when configured); Google/Outlook/Exchange OAuth P4.
- [ ] Real LLM provider abstraction.
- [ ] Load test to 15k events/s (p95 ≤ 3s).
- [ ] DLQ replay UI / admin tooling.
- [ ] Shadow/canary analytics dashboards.
- [ ] Full tenant SSO/branch policy enforcement.

*Sources:* `docs/implementation_status_tas.md` §4, §7.

### Code-quality / bug-audit items

- [x] Fix doc-request workflow verification to default to `False`.
- [x] Tighten `wf_id` and `trigger_id` types at API call sites (`/events/document-received` validates the resolved `workflow_id`).
- [ ] Add locks or Redis backing for in-memory idempotency/loop-budget stores.
- [ ] Wire `app/persistence/redis_store.py` into idempotency and rule cache paths.

*Sources:* `docs/BUG_AUDIT_REPORT.md` §5; `docs/implementation_status_tas.md` §8.

### Configuration to set

- [ ] `CORS_ORIGINS`, `DATABASE_URL`, `REDIS_URL`.
- [ ] `KAFKA_BOOTSTRAP_SERVERS` and `KAFKA_CONSUMER_ENABLED=true`.
- [ ] `TEMPORAL_HOST`, `TEMPORAL_NAMESPACE`, mTLS certs.
- [ ] `CRM_BASE_URL`, `CRM_API_KEY`.
- [ ] `GOOGLE_CLIENT_*`, `MICROSOFT_CLIENT_*` for calendar.
- [ ] `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`.

---

## 7. Integration / Cross-Agent Orchestration

- [ ] Wire real cross-agent HTTP clients:
  - FUS → CMS: `POST /api/v1/precheck` before any send.
  - FUS → TAS/Timing: best-time and enrollment signals.
  - TAS → FUS: emit `deal.stage_changed` and `task.completed` events.
  - PAS → FLS: feed `deal.funded` / `deal.lost` outcomes for feedback labels.
  - CMS → all: consume activity events and publish `compliance.signal`.
- [ ] Add retry, circuit-breaker, and timeout policies on all cross-agent and third-party HTTP calls.
- [ ] Add real Kafka consumers for event-driven flows (e.g., TAS consumes `deal.stage_changed`; FUS consumes `lead.scored`; PAS consumes `outreach.sent`/`outcome` events).
- [ ] Add shared compose/manifest services for Postgres, Redis, Kafka (broker + KRaft), Schema Registry, Prometheus, Grafana, and OpenTelemetry Collector.

*Sources:* `docs/agent_communication.md`; `docs/DEPLOYMENT_READINESS.md` §Deployment Checklist.

---

## 8. Deployment & Operations

### Staging environment

- [ ] Provision Postgres for CMS, FUS, PAS, TAS persistence; FLS may share or keep disk/SQLite for smaller staging.
- [ ] Provision Redis and configure all agents with real `REDIS_URL`.
- [ ] Provision Kafka (broker + KRaft) and enable producers/consumers.
- [ ] Add auth middleware on every agent and the gateway.
- [ ] Add `/ready` probes that verify database and upstream dependency health.
- [ ] Add Prometheus/Grafana metrics and OpenTelemetry tracing.

### Production environment

- [ ] Provision S3 WORM bucket and wire 7-year audit archival.
- [ ] Run load tests verifying all NFRs.
- [ ] Add circuit breakers and retries on all third-party HTTP calls.
- [ ] Run containers as non-root.
- [ ] Restrict CORS origins.
- [ ] Enable TLS termination and network policies.
- [ ] Add automated DB migrations (Alembic).
- [ ] Write SOC 2 evidence runbooks.

*Sources:* `docs/DEPLOYMENT_READINESS.md` §Deployment Checklist, §Immediate Blockers; `docs/BUG_AUDIT_REPORT.md` §7.3.

---

## 9. Testing & Quality

- [ ] Add gateway proxy tests for every agent (404, upstream failure, auth pass-through, file downloads).
- [ ] Add end-to-end cross-agent tests.
- [ ] Add `locust` or `k6` load tests for all five agents.
- [ ] Add contract tests for cross-agent HTTP calls and Kafka event schemas.
- [ ] Add nightly drift/label tests in CI for FLS.
- [ ] Keep `ruff`, `mypy`, and pytest passing as the codebase expands.

---

## 10. Quick-Reference: Environment Variables to Configure in Production

| Variable | Used by | Purpose |
|----------|---------|---------|
| `API_KEY` | All agents + gateway | Replace default `change-me-dev-key` with a vault-managed key. |
| `CORS_ORIGINS` | All agents | Restrict to the production web origin. |
| `DATABASE_URL` | CMS, FUS, PAS, TAS, gateway | Postgres URL with migrations. |
| `REDIS_URL` | All agents | Production Redis endpoint; disables in-memory fallback. |
| `KAFKA_BOOTSTRAP_SERVERS` | CMS, FUS, TAS | Real Kafka broker for producers and consumers. |
| `KAFKA_PRODUCE` / `KAFKA_INGEST` / `KAFKA_CONSUMER_ENABLED` | CMS, FUS, TAS | Toggle real Kafka vs. in-process fallback. |
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
| `CRM_BASE_URL` / `CRM_API_KEY` | TAS | Real CRM service. |
| `GOOGLE_CLIENT_*` / `MICROSOFT_CLIENT_*` | TAS | Calendar OAuth. |
| `LLM_PROVIDER` / `LLM_API_KEY` / `LLM_MODEL` | TAS | LLM provider. |
| `TEMPORAL_HOST` / `TEMPORAL_NAMESPACE` | TAS, FUS | Temporal cluster. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | All | OpenTelemetry backend. |

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
