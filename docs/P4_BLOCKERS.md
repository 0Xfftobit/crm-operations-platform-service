# P4 Development Blockers

This file lists the remaining work that is blocking promotion of the CRM Operations Platform Service from the P0–P3 implementation (plus the first P4 hardening pass) to a production-ready P4 (Hardening / GA Launch) state.

## Status Summary

- **P0–P3 implementation:** complete for all five engines.
- **First P4 hardening pass:** complete. Bug-audit fixes, thread-safe in-memory state, cross-agent HTTP clients, `X-Request-ID` propagation, and default-dev-key rejection are all in place.
- **Latest P4 hardening pass (2026-08-10):** resolved the code-level production blockers below:
  - CORS wildcard now rejected in non-local environments for all agents and the gateway.
  - `/ready` probes now check real dependencies (Postgres, Redis, storage paths) and the gateway probes upstream `/ready` first.
  - A `mypy` gate was added to CI for the gateway (`src/api`, `config`); per-agent mypy will follow.
  - A new bug was found/fixed: FUS `abtest.py` used MD5 for variant allocation and was upgraded to SHA-256.
  - Kafka producer/consumer bus is now code-complete in all five agents (canonical/short topics, per-deal/lead/contact partition keys, in-memory fallback, and contract tests).
- **Remaining blockers:** production infrastructure integration, real external service endpoints, production auth/RBAC, load testing, and operational runbooks (see sections below).

---

## 1. Persistence Layer

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| All engines default to in-memory stores | Data is lost on restart; no multi-worker safety even with locks | `STORAGE_BACKEND=memory` or equivalent is the default in every agent |
| Postgres support per agent | Durable persistence is required for production | CMS ✅ (SQLAlchemy ORM + Alembic); TAS ✅ (inline SQL schema); FUS ✅ (SQLAlchemy ORM + Alembic); PAS ✅ (SQLAlchemy ORM + Alembic); FLS ✅ (SQLAlchemy ORM + Alembic). Default in all agents is `memory`; enable with `STORAGE_GATEWAY=postgres` and `DATABASE_URL`. |
| Postgres migrations not run in production | Schema must be managed with Alembic/DDL | CMS/FUS/PAS/FLS have Alembic; TAS auto-creates schema on first connection. Production must run `alembic upgrade head` (or `DB_AUTO_CREATE=true` only in dev). |
| SQLite/Postgres switchover untested | Some engines use SQLite in tests; production needs Postgres load tests | CMS/FUS/PAS/FLS validated with SQLite-backed tests and Alembic smoke tests; all can run with `STORAGE_GATEWAY=postgres` and `DATABASE_URL=sqlite:///...`. Live Postgres load/integration tests remain P4. |
| S3/DW export for PAS | CSV/PDF export is local; production needs S3/DW sink | `app/pdf.py` writes local files only; `DW_*` and `S3_EXPORT_*` env vars documented but not wired |
| CMS WORM/cold store needs real S3 | Tamper-evidence relies on `local` WORM; S3 Object Lock not validated | `WORM_GATEWAY=local`, `COLD_STORE_GATEWAY=local` default |

## 2. Caching & Shared State

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| Redis not enabled by default | In-memory caches are per-process and cannot be shared across workers | `CACHE_GATEWAY=memory` / `REDIS_URL` unset in default `.env.example`; Redis containers in `--profile infra` |
| FUS `RedisFrequencyStore` tested | Frequency counters in Redis need integration tests | `CACHE_GATEWAY=redis` works with `RedisFrequencyStore`; `tests/test_redis_frequency_store.py` exists |
| CMS `CACHE_GATEWAY=redis` consent cache | Shared consent cache needs real Redis | `app/cache.py` supports Redis; `tests/test_redis_consent_cache.py` exists |
| TAS Redis idempotency/rule cache | Shared idempotency and rule cache need real Redis | `app/persistence/redis_store.py` exists; `IDEMPOTENCY_BACKEND=redis` selectable; tests use fakeredis |
| PAS Redis integration | Shared caches for hot data need real Redis | Test suites mock or use in-memory fallback |

## 3. Eventing & Streaming

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| Kafka producers/consumers are code-complete | In-process bus cannot scale or survive restarts | `app/messaging/kafka_bus.py` implemented in all five agents; canonical/short topics, per-deal partition keys, in-memory fallback, broker-free contract tests; real broker needs `KAFKA_PRODUCE=true` / `KAFKA_CONSUMER_ENABLED=true` and a live Redpanda/Kafka cluster |
| Per-deal ordering is implemented and tested | Deal-level events must be ordered by partition key | All `produce()` calls compute a `deal_id`/`lead_id`/`contact_id` partition key; contract tests validate it |
| Kafka production hardening | Real clusters need security, schemas, and observability | No Schema Registry client, no SASL/SSL config, no ACLs, no standardized DLQ, no offset-commit strategy, no Kafka lag metrics |
| TAS Temporal disabled | Document workflows and durable timers use in-process fallback | `TEMPORAL_ENABLED=false` default; Temporal client/worker code exists |
| FUS scheduler uses in-process tick | Temporal durable timers not used in production | `scheduler.py::tick()` is in-process; Temporal workflow code exists but disabled |
| Cross-agent event fan-out (Kafka baseline complete) | TAS → FUS, PAS → FLS, FUS → CMS use synchronous HTTP; async Kafka now wired | HTTP fan-out is wired and unit-tested; Kafka topic contract and bus code in place; live end-to-end validation still needed |

## 4. External Service Integrations

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| FUS real channel providers | Twilio, SendGrid, WhatsApp Business not validated | `app/providers.py` has HTTP clients with single-provider retry; falls back to mock when credentials are unset; multi-provider failover not implemented |
| TAS CRM/Calendar/Feature store | Real endpoints not wired | `app/integrations/crm.py`, `calendar.py`, `feature_store.py` call `*_SERVICE_URL` when set, otherwise stub; OAuth not implemented |
| PAS data warehouse integration | Attribution/reconciliation use in-memory finance SoT | `DW_*` env vars are documented but not wired to a real DW |
| CMS Spark batch disparity | `jobs/disparity_batch.py` needs real Spark cluster | `app/spark_batch.py` unit-tested pure aggregation; not run on Spark/Flink |
| FLS MLflow model registry | Model lineage uses in-memory artifact store | `MLFLOW_TRACKING_URI` / `MLFLOW_API_KEY` are in `.env.example` but not used; model registry in-memory only |

## 5. Security, Auth & RBAC

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| Default dev key must be replaced | `change-me-dev-key` is rejected in non-local envs, but no vault integration exists | `API_KEY` is a single shared secret from `.env` |
| No OAuth/SSO | Production requires identity provider integration | `X-Role` header only; no JWT/OAuth middleware |
| Tenant/branch isolation is partial | `tenant_id`/`branch_id` fields exist but isolation/SSO is P4 | `app/rbac.py` is role/scope helpers only |
| CORS origins unrestricted | `CORS_ORIGINS=*` rejected in non-local envs; still defaults to `http://localhost:3000` | Must be restricted to production web origin in production |
| No audit log tamper monitoring in prod | Merkle anchoring and cold-store purge are in code but not wired to S3 | `WORM_GATEWAY=local` / `COLD_STORE_GATEWAY=local` |

## 6. Gateway & Cross-Agent

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| Limited proxy integration tests | Need tests beyond `/health` and `/agents` | `tests/integration/test_gateway.py` and `tests/integration/test_gateway_cross_agent.py` have 14 tests covering 502/504/404, auth, header pass-through, RLS header forwarding, request-id, and upstream ready ok/degraded; file download test missing |
| End-to-end cross-agent tests | FUS → CMS, TAS → FUS, PAS → FLS flows need live container-to-container CI | `test_cross_agent.py` and `test_requirements.py` cover the HTTP client contracts with mocked `httpx.Client`; no live multi-container tests |
| Gateway timeout per agent | All agents need configurable timeouts | `settings.agent_timeout(agent)` is wired with per-agent defaults |

## 7. Load, Resilience & Observability

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| No load test to 15k/s | P4 hardening target is p95 ≤ 3s at 15k events/s | `test_load_smoke_burst` is a small smoke test only; no 15k/s or FUS 2k msg/s harness |
| Retry/circuit-breaker policies | Cross-agent and third-party HTTP calls can fail silently | FUS providers retry on 429/transient failures; TAS activities have retry/backoff; cross-agent `httpx` clients use a single attempt; `app/core/resilience.py` in all agents provides `ResilientClient` and `CircuitBreaker` (3-attempt exp. backoff, open after 3 failures, 30s timeout); cross-agent clients (FUS→CMS, PAS→FLS, TAS→FUS) now use it |
| OpenTelemetry not enabled by default | Traces/metrics are no-op unless `TRACING_ENABLED=true` | CMS has OTel stub with graceful degradation; FUS/TAS have config fields; PAS/FLS have no OTel code; Collector in compose `--profile infra` |
| Prometheus metrics endpoints | Prometheus cannot scrape anything without `/metrics` | Prometheus/Grafana/OTel containers in `--profile infra`; the gateway and all agents expose `/metrics` (gateway) or `/api/v1/metrics` with in-memory fallback; Grafana/SLO dashboards remain uncommitted |
| Grafana dashboards | No production dashboards | Provisioning files exist but no dashboard JSON committed; no SLO recording rules or alerting |
| DLQ replay UI | Replay API exists; admin UI is P4 | `POST /api/v1/vision/dlq/{id}/replay` only; TAS DLQ exists; FLS has `_dead_letter` list; no standardized DLQ topic |

## 8. Operational Readiness

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| No production runbooks | Incident response, scaling, rollback procedures missing | Only architecture/implementation docs exist |
| SOC 2 / fair-lending compliance runbooks | Compliance evidence procedures not documented | Guardrails and ledger in place; runbooks are P4 |
| No staged canary/shadow analytics dashboard | `canary_stats` is API-only | Dashboards deferred to GA Launch |
| Backup/DR strategy for Postgres/Redis/Kafka | Persistence defaults to memory; no backup plan | Not documented; Postgres/Redis/Redpanda volumes in compose but no backup/DR runbooks |

## 9. Type / Tooling Debt

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| mypy `NoDecode` warnings | Spurious type errors cascade; may indicate version mismatch | All agents import `NoDecode` from `pydantic_settings`; mypy may still flag it in CI depending on version |
| mypy gate partial | Type errors can reach production | CI runs `ruff`; a `mypy` gate exists for the gateway (`src/api`, `config`); per-agent `mypy` will follow |

## 10. Deployment & Packaging

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| Docker Compose is local dev only | No Kubernetes manifests, autoscaling, or secrets management | `docker-compose.yml` with `--profile infra` provides Postgres/Redis/Redpanda/Prometheus/Grafana/OTel; no K8s manifests or Helm |
| AWS/GCP/Azure deployment docs | Only CMS has a `deploy/README.md`; other agents do not | `src/agents/follow_up_sequences/deploy/README.md` and CMS deploy doc exist |
| Vault integration for secrets | Keys and DB credentials in `.env` | `AWS_*` / `SENDGRID_API_KEY` / `TWILIO_*` are plain env vars |

---

## Recommended P4 Roadmap Order

1. **Persistence:** enable Postgres for CMS/TAS/FUS/PAS/FLS (all have SQL repositories/ORM/migrations or inline schema); run `alembic upgrade head` for FUS/PAS/FLS; add live Postgres integration/load tests.
2. **Redis:** enable `CACHE_GATEWAY=redis` / `REDIS_URL` in CI for CMS/FUS and `IDEMPOTENCY_BACKEND=redis` for TAS; add Redis integration tests.
3. **Kafka/Redpanda:** producer/consumer bus code, canonical topics, and per-deal partition-keying are done; deploy a live Redpanda cluster, enable `KAFKA_PRODUCE`/`KAFKA_CONSUMER_ENABLED`, and run end-to-end cross-agent fan-out tests; add Schema Registry, SASL/SSL/ACLs, DLQ, offset-commit, and metrics.
4. **Temporal:** deploy Temporal cluster; enable `TEMPORAL_ENABLED` for TAS doc workflows and wire FUS `scheduler.py` to Temporal durable timers.
5. **External services:** obtain real credentials for Twilio, SendGrid, WhatsApp Business, CRM, calendar, feature store, DW, S3, MLflow; add contract/integration tests.
6. **Auth/RBAC:** implement OAuth/JWT middleware and tenant/branch SSO/policy enforcement.
7. **Load/resilience/observability:** add retry and circuit-breaker to cross-agent clients (done), run 15k/s and 2k msg/s load tests, expose Prometheus `/metrics` in all agents (done), enable OpenTelemetry tracing, create Grafana dashboards and SLO alerting.
8. **Operational docs:** write runbooks, SOC 2 evidence, canary/shadow dashboards, and backup/DR plans.
9. **Deployment:** add Kubernetes manifests, vault/secret management, and multi-cloud packaging.

---

## See Also

- `docs/RemainingWork.md` — full remaining implementation checklist.
- `docs/DEPLOYMENT_READINESS.md` — per-agent readiness and production blockers.
- `docs/implementation_status_*.md` — P0–P3 + P4 status for each agent.
- `docs/ENGINE_TESTING_RESULTS.md` — latest test results.
