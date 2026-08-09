# Deployment Readiness Assessment

**Scope:** `crm-operations-platform-service` — all five agent engines and the top-level gateway.  
**Assessment date:** 2026-08-09

## Overall Verdict

The repository is **ready for local development, integration testing, staged demonstrations, and CI** on a laptop or CI runner. **P0–P3 business logic is implemented and tested across all five agents, and a first P4 hardening pass is now complete.** A first P4 pass added shared infrastructure in `docker-compose.yml`, gateway auth, per-agent timeouts, Redis-backed frequency counters in FUS, cross-agent HTTP fan-out clients (FUS → CMS, PAS → FLS, TAS → FUS), real channel providers with mock fallback in FUS, HTTP fallback CRM/calendar/feature-store clients in TAS, per-sequence frequency caps and idempotency in FUS, and bug-audit fixes in CMS, PAS, and FLS. The latest P4 pass added FUS suppression (`SuppressionEvent` and `POST /api/v1/webhooks/suppression`) with permanent contact suppression and `contact.suppressed` events, LO/owner reply notifications with optional HTTP service and in-process log fallback, `threading.RLock()` on all FUS repository methods, `X-Request-ID` propagation in the FUS CMS precheck client, and the gateway now generates/propagates `X-Request-ID` on every proxy response (including 502/404/504) with root integration tests. This CMS P4 hardening pass added `threading.RLock()` to every in-memory global store and repository, exact-year retention in `service.audit_purge` (`privacy._add_years`), fractional `days_left` for disclosure/retention timers, and `CaseUpdate.status`/`casework.update_case` typed as `Literal`. **All engine test suites and lint checks are green (see `docs/ENGINE_TESTING_RESULTS.md`).** It is still **not ready for production deployment** until the remaining blockers below are addressed.

| Readiness Area | Status | Score |
|----------------|--------|------:|
| Code builds and tests pass | Green | 100% |
| Gateway routing + auth | Green | 85% |
| Core business logic correct | Green | 95% |
| Cross-agent HTTP clients | Green | 75% |
| Authentication / authorization | Yellow | 70% |
| Persistence | Yellow | 40% |
| Kafka / event-driven flows | Yellow | 60% |
| Redis / shared caching | Yellow | 60% |
| Observability / metrics | Yellow | 50% |
| Production packaging / security | Yellow | 50% |

---

## What Is Implemented Now (P0–P3 + First P4 Pass)

- All five engines run as self-contained FastAPI services and each has a passing test suite.
- The gateway now enforces `X-API-Key`, proxies all HTTP methods, forwards all non-hop-by-hop headers, supports per-agent timeouts, and returns `404`/`502`/`504` where appropriate.
- `docker-compose.yml` now includes an optional `--profile infra` stack with Postgres, Redis, Redpanda (Kafka), Schema Registry, Prometheus, Grafana, and an OpenTelemetry Collector.
- `follow_up_sequences` uses Redis-backed `FrequencyStore` when `CACHE_GATEWAY=redis` and can call the Compliance Monitor precheck before a send.
- `performance_analytics` can fan out `loan.funded` / `deal.lost` outcomes to the Feedback Loop.
- `task_automation` can fan out `deal.stage_changed` and `lead.assigned` events to the Follow-up Sequences engine.
- `.env.example` files have been updated with the new cross-agent and infrastructure variables.
- **follow_up_sequences** now enforces UTC-normalized frequency-cap day/week keys, per-sequence frequency caps, a 5-minute pre-send idempotency guard, `Literal` state validation, and `Field(default_factory=dict)` for mutable request defaults; `app/providers.py` has real Twilio/SendGrid/WhatsApp Business HTTP clients that fall back to a mock provider when credentials are unset.
- **task_automation** now has HTTP fallback CRM, calendar, and feature-store clients in `app/integrations/crm.py`, `calendar.py`, and `feature_store.py`; `document_received` validates `workflow_id` and rejects non-string ids; `doc_request_chain.complete_verification` defaults to `verified=False`; `normalize_trigger`, `run_pipeline_stages`, and `process_event` assert `trigger_id` is a `str`; every in-memory global (`store`, `runs`, `ledger`, `events`, `event_history`, `dlq`, `metrics`, `guards`, `escalation`, `cron_triggers`, `kafka_consumer`, `kafka_consumer_group`, `messaging/kafka_bus`, `messaging/trigger_ingestor`, `integrations/temporal`, `integrations/activity_runner`, `integrations/feature_store`, `workflows/doc_request_chain`, `tracing`, `agents/llm_task_agent`, `persistence/memory_store`, `persistence/redis_store`) is protected by `threading.RLock()`.
- **performance_analytics** bug-audit fixes landed in `reconciliation._funded_amount_for_period` (ISO-8601 duration windows via `_in_period`), `dashboard_builder._in_period_or_skip`, `attribution._time_decay` (uses actual `funded_at`), `AttributionRepository._items` tuple-key typing, `build_channel_effectiveness` duplicate-branch removal, and `typing.List` annotations to avoid `list` method shadowing; all in-memory repositories now use `threading.RLock()`.
- **compliance_monitor** bug-audit fixes landed in coverage-loop interval handling, `http_source.poll_once` cursor hardening, `CaseUpdate.status`/`service._raise` typing, exact-year retention, float `days_left`, and `coldstore.py` S3/local `encoding="utf-8"` and single pagination.
- **feedback_loop** bug-audit fixes are in place: A/B split sums to 100%, guardrail fallback only fires when no guardrail metric is configured, SHA-256 variant hashing, `timedelta` drift window, `joblib` serialization, `_precision_recall_f1` 4-tuple return type, and all in-memory repositories plus the drift reference store now use `threading.RLock()`.
- Root integration tests in `tests/integration/test_gateway.py` cover auth, unknown agent, unconfigured upstream, production default-key rejection, and `X-Request-ID` generation/propagation (`test_gateway_generates_and_returns_request_id`, `test_gateway_propagates_existing_request_id`).
- **follow_up_sequences** now has a `SuppressionEvent` model and `POST /api/v1/webhooks/suppression` endpoint that permanently suppresses a contact on bounce/complaint/spam/block events, stops active enrollments, and emits `contact.suppressed`; `guardrails.check` stops sends on `contact.suppressed`; `Contact` includes `suppressed`, `suppressed_reason`, and `owner_id`.
- **follow_up_sequences** now notifies the assigned LO/owner on inbound reply via `app/notifications.py:notify_owner_of_reply`, with an HTTP `POST` to `NOTIFICATION_SERVICE_URL` when configured and a fallback to an in-process log.
- **follow_up_sequences** now wraps all repository methods with `threading.RLock()` via `app/repositories.py:_synchronized` and also protects the in-process event log (`app/events.py`), fallback notification log (`app/notifications.py`), mock provider send log (`app/providers.py`), and `FrequencyStore` counters (`app/frequency.py`) with `threading.RLock()`.
- **follow_up_sequences** CMS precheck client (`app/clients/compliance_client.py`) sends `X-Request-ID` on every call.
- **task_automation** FUS fan-out client (`app/clients/followup_client.py`) and **performance_analytics** FLS fan-out client (`app/clients/feedback_client.py`) now send `X-Request-ID`; the TAS client deduplicates header building through a shared `_headers()` helper.
- The API gateway generates/propagates `X-Request-ID` and returns it on every proxied response, including 502/504/404 responses.
- `docker-compose.yml` now wires the cross-agent fan-outs by default: `FUS_PRECHECK_ENABLED=true` + `COMPLIANCE_MONITOR_URL` for FUS, `OUTCOME_FANOUT_ENABLED=true` + `FEEDBACK_LOOP_URL` for PAS, and `FUS_FANOUT_ENABLED=true` + `FOLLOW_UP_SEQUENCES_URL` for TAS.
- All five agents plus the gateway reject the default `change-me-dev-key` in non-local environments; production still requires a vault-managed key and per-tenant RBAC is not implemented.

---

## Per-Agent Ticket Coverage

### Compliance Monitor System (CMS)

**Ticket:** `docs/ticket_description_cms.md`  
**Implementation status:** `docs/implementation_status_cms.md`

| Requirement | Status | Notes |
|-------------|--------|-------|
| Pre-send compliance hook | Done | `POST /precheck` with TCPA quiet-hours, opt-out, DNC, CAN-SPAM |
| Consent management per lead/channel | Done | `PUT /consent`, optional Redis hot cache |
| Immutable hash-chained audit ledger | Done | `app/audit.py`, `app/merkle.py`, `GET /audit/verify` |
| Fair-lending disparity (4/5ths) | Done | `POST /fairness/evaluate` |
| ECOA disclosure clocks | Done | `app/disclosures.py` |
| PII scan / data privacy | Done | `app/pii.py`, `app/privacy.py` |
| Case/alert management | Done | `app/casework.py` |
| Rule versioning + two-step approval | Done | `app/rules.py`, `app/api/rules.py` |
| Kafka producer/consumer | Partial | `app/kafka_bus.py` supports optional `aiokafka` with graceful fallback |
| S3 WORM cold storage | Partial | `app/worm.py`/`app/coldstore.py` support `s3` gateway; local fallback by default |
| Production auth / RBAC | Partial | X-API-Key + X-Role for export; default `change-me-dev-key` rejected outside local/dev/test |
|| Bug-audit hardening | Done | `threading.RLock()` on in-memory globals + repositories; exact-year retention; fractional `days_left`; case-status `Literal` |

### Feedback Loop System (FLS)

**Ticket:** `docs/ticket_description_fls.md`  
**Implementation status:** `docs/implementation_status_fls.md`

| Requirement | Status | Notes |
|-------------|--------|-------|
| Prediction/outcome/override/feedback capture | Done | `POST /predictions`, `/outcomes`, `/feedback` |
| Point-in-time label building | Done | `app/label_builder.py` |
| Per-engine metrics and drift | Done | `app/aggregator.py`, `app/drift.py` |
| Reproducible retraining + fairness gate | Done | `app/model_retrainer.py` |
| Champion/challenger promotion + rollback | Done | `POST /models/{engine}/promote`, `/rollback` |
| A/B test framework | Done | `app/abtest.py` |
| MLflow model registry | Not done | In-memory `ModelVersionRepository` |
| Kafka event capture | Not done | REST only |
| Production persistence | Not done | `STORAGE_BACKEND=memory|disk` only |
|| Bug-audit hardening | Done | A/B split sums to 100%, guardrail fallback fixed, SHA-256 hash, `timedelta` drift window, `joblib` serialization, repository RLock | |

### Follow-up Sequences Engine (FUS)

**Ticket:** `docs/ticket_description_fus.md`  
**Implementation status:** `docs/implementation_status_fus.md`

| Requirement | Status | Notes |
|-------------|--------|-------|
| Sequence builder (steps, branches, exits) | Done | `SequenceDef`/`Step`/`Branch` models |
| Enrollment/de-duplication | Done | `service.enroll()` |
| Quiet-hours / best-time scheduler | Done | `app/scheduler.py`, `app/quiet_hours.py` |
| Consent/DNC/frequency-cap guardrails | Done | `app/guardrails.py`, `app/frequency.py` |
| Redis-backed frequency counters | Done | `RedisFrequencyStore` when `CACHE_GATEWAY=redis` |
| Engagement-driven branching | Done | `app/branching.py` |
| Stop conditions | Done | reply/apply/opt-out/max-steps |
| Personalization variables | Done | `app/personalize.py` |
| CMS precheck HTTP client | Done | `app/clients/compliance_client.py` |
| Real channel providers | Partial | Twilio/SendGrid/WhatsApp Business HTTP clients in `app/providers.py` with mock fallback when unconfigured |
| Kafka consumer for `lead.scored`/`deal.stage_changed` | Not done | In-process `app/events.py` |
| Postgres durable persistence | Not done | `STORAGE_GATEWAY=memory|postgres` optional |
|| Suppression webhook / permanent suppression | Done | `SuppressionEvent` + `POST /api/v1/webhooks/suppression`; `service.apply_suppression` (`app/service.py:365-383`); in-process per-tenant list |
|| LO/owner reply notifications | Done | `Contact.owner_id` + `app/notifications.py:notify_owner_of_reply`; optional HTTP service with in-process log fallback |
|| Repository + in-memory state thread-safety | Done | `threading.RLock()` via `_synchronized` in `app/repositories.py`; `RLock` in `app/events.py`, `app/notifications.py`, `app/providers.py`, and `app/frequency.py` |

### Performance Analytics System (PAS)

**Ticket:** `docs/ticket_description_pas.md`  
**Implementation status:** `docs/implementation_status_pas.md`

| Requirement | Status | Notes |
|-------------|--------|-------|
| Touch/outcome/cost/finance ingestion | Done | `app/metrics_collector.py` |
| Governed semantic metric layer | Done | `app/semantic.py` |
| Funnel, scorecards, cohorts, leaderboard | Done | `app/dashboard_builder.py` |
| Multi-touch attribution (6 models) | Done | `app/attribution.py` |
| Channel effectiveness + recommendations | Done | `app/dashboard_builder.py` |
| Reconciliation to finance SoT | Done | `app/reconciliation.py` |
| Threshold alerts + scheduled reports | Done | `app/anomaly_detector.py` |
| CSV/PDF export | Done | `app/pdf.py` |
| Bug-audit fixes: ISO reconciliation, time-decay conversion, type shadowing | Done | `app/reconciliation.py:24-36`, `app/period_utils.py:48-88`, `app/attribution.py:74-87`, `app/repositories.py` |
| Repository + in-memory state thread-safety | Done | `threading.RLock()` via `_synchronized` on every PAS repository (`app/repositories.py`) |
| Unit + functional tests | Done | `42/42` passing, including `tests/test_bug_audit.py` |
| FLS outcome fan-out | Done | `app/clients/feedback_client.py` |
| Streaming/Kafka ingestion | Not done | REST only |
| Production persistence | Not done | In-memory repositories |
| Full RLS/SSO | Not done | Header-driven filters only |

### Task Automation Engine (TAS)

**Ticket:** `docs/ticket_description_tas.md`  
**Implementation status:** `docs/implementation_status_tas.md`

| Requirement | Status | Notes |
|-------------|--------|-------|
| JSONLogic/CEL rule engine | Done | `app/engine/rule_engine.py` |
| Stage-driven task/document creation | Done | `app/engine/action_executor.py` |
| Business-hours SLA + escalation | Done | `app/business_hours.py`, `app/escalation.py` |
| Idempotency + action ledger | Done | `app/idempotency.py`, `app/ledger.py` |
| Dry-run / canary / rollback | Done | `app/service.py`, `app/guards.py` |
| LLM task agent with approval gate | Done | `app/agents/llm_task_agent.py` (stub) |
| DLQ + replay | Done | `app/dlq.py` |
| FUS fan-out client | Done | `app/clients/followup_client.py` |
| Optional Kafka consumer group | Partial | `app/kafka_consumer_group.py` off by default |
| Redis idempotency store | Partial | `app/persistence/redis_store.py` exists; defaults to memory |
| Real CRM/calendar/feature-store integrations | Partial | HTTP clients in `app/integrations/crm.py`, `calendar.py`, `feature_store.py` with in-process stubs when unconfigured |
| Bug-audit fixes: doc verification, trigger id, thread-safety | Done | `app/workflows/doc_request_chain.py:210-213`, `app/messaging/trigger_ingestor.py:55-57`, `app/pipeline.py:57-58`, `app/service.py:61-62,301-302`, `app/store.py`, `app/runs.py`, `app/ledger.py`, `app/events.py`, `app/persistence/memory_store.py`, `app/persistence/redis_store.py`, and 12+ other modules |
| Unit + functional tests | Done | `78/78` passing (1 skipped), including `tests/test_bug_audit.py` |
| Production auth / RBAC | Partial | X-API-Key + X-Role headers; no OAuth/SSO |

---

## Deployment Checklist

### Local / CI

- [x] `docker compose up --build` starts the gateway and all five agents.
- [x] All engine test suites pass (CMS 109, FLS 25, FUS 50, PAS 35, TAS 72 + 1 skipped) and `ruff` lint is green.
- [x] Gateway `/health` and `/agents` respond.
- [x] Gateway `/ready` checks upstream agent health endpoints.
- [x] Root integration tests cover auth, 404, 502, default-key rejection, and `X-Request-ID` generation/propagation.
- [ ] End-to-end cross-agent tests through the gateway.
- [ ] Full integration test suite with real Postgres/Redis/Kafka.

### Staging

- [ ] Provision Postgres for CMS, FUS, PAS, TAS persistence.
- [ ] Provision Redis for hot caches (consent, frequency, idempotency).
- [ ] Provision Kafka (or Redpanda) for event-driven flows.
- [ ] Add Schema Registry / canonical event schemas.
- [ ] Add auth middleware on every agent and the gateway.
- [ ] Add `/ready` probes that verify DB and upstream dependency health.
- [ ] Add Prometheus/Grafana metrics and OpenTelemetry tracing.
- [ ] Configure `*_URL` env vars for cross-agent HTTP calls.

### Production

- [ ] Secrets manager for all API keys and database credentials.
- [ ] S3 WORM bucket for 7-year audit retention.
- [ ] Load tests verifying all NFRs.
- [ ] Circuit breakers and retries on all third-party HTTP calls.
- [ ] Non-root container users for all agents.
- [ ] Restricted CORS origins.
- [ ] TLS termination and network policies.
- [ ] Automated DB migrations (Alembic).
- [ ] SOC 2 evidence runbooks.

---

## Immediate Blockers for Production

1. **Authentication is a shared single key, not per-tenant/per-user RBAC** — all agents and the gateway now reject the default `change-me-dev-key` in non-local environments, but they still share one `X-API-Key` and have no OAuth2, tenant scopes, or row-level access control.
2. **No production persistence** — most agents use in-memory stores by default; S3 WORM archive not wired.
3. **No real Kafka cluster** — producer/consumer code exists but is not always enabled; exactly-once processing not wired.
4. **No observability** — Prometheus/Grafana and OpenTelemetry services are defined in compose but the apps do not expose metrics/traces yet.
5. **No secrets management** — default dev key and DB URLs in `.env` files.
6. **Third-party and cross-agent integrations are optional** — FUS channel providers and TAS CRM/calendar/feature-store clients fall back to stubs when credentials/URLs are unset. Production credentials and real endpoint contracts are still required.
7. **Readiness probes do not check real dependencies** (Postgres, Redis, Kafka, CRM, calendar).
