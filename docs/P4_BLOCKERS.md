# P4 Development Blockers

This file lists the remaining work that is blocking promotion of the CRM Operations Platform Service from the P0–P3 implementation (plus the first P4 hardening pass) to a production-ready P4 (Hardening / GA Launch) state.

## Status Summary

- **P0–P3 implementation:** complete for all five engines.
- **First P4 hardening pass:** complete. Bug-audit fixes, thread-safe in-memory state, cross-agent HTTP clients, `X-Request-ID` propagation, and default-dev-key rejection are all in place.
- **Remaining blockers:** production infrastructure integration, real external service endpoints, production auth/RBAC, load testing, and operational runbooks.

---

## 1. Persistence Layer

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| All engines default to in-memory stores | Data is lost on restart; no multi-worker safety even with locks | `STORAGE_BACKEND=memory` or equivalent is the default in every agent |
| Postgres migrations not run in production | Schema must be managed with Alembic/DDL | Agents have `app/persistence/db.py` and Alembic configs, but compose defaults to in-memory |
| SQLite/Postgres switchover untested | Some engines use SQLite in tests; production needs Postgres load tests | CMS uses SQLite tests; PAS has no SQL persistence; FUS/FLS/TAS repositories are in-memory |
| S3/DW export for PAS | CSV/PDF export is local; production needs S3/DW sink | `app/pdf.py` writes local files only |
| CMS WORM/cold store needs real S3 | Tamper-evidence relies on `local` WORM; S3 Object Lock not validated | `WORM_GATEWAY=local`, `COLD_STORE_GATEWAY=local` default |

## 2. Caching & Shared State

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| Redis not enabled by default | In-memory caches are per-process and cannot be shared across workers | `CACHE_GATEWAY=memory` / `REDIS_URL` unset in default `.env.example` |
| FUS `RedisFrequencyStore` not exercised in CI | Frequency counters in Redis need integration tests | `CACHE_GATEWAY=redis` only manual; no Redis-specific test stage |
| PAS/TAS/CMS Redis integration tests | Shared caches for idempotency, rules, and hot data need real Redis | Test suites mock or use in-memory fallback |

## 3. Eventing & Streaming

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| Kafka producers/consumers are stubs | In-process bus cannot scale or survive restarts | `KAFKA_PRODUCE=false` / `KAFKA_CONSUMER_ENABLED=false` in all agents |
| Per-deal ordering not validated | Deal-level events must be ordered by partition key | `kafka_consumer_group.py` exists but off by default; no partition-keying tests |
| TAS Temporal disabled | Document workflows and durable timers use in-process fallback | `TEMPORAL_ENABLED=false` default |
| FUS scheduler uses in-process tick | Temporal durable timers not used in production | `scheduler.py::tick()` is in-process |
| Cross-agent event fan-out only HTTP | TAS → FUS, PAS → FLS, FUS → CMS use synchronous HTTP; async Kafka not used | Fan-out is HTTP with env flags; no Kafka topic wiring |

## 4. External Service Integrations

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| FUS real channel providers | Twilio, SendGrid, WhatsApp Business not validated | `app/providers.py` has HTTP clients that fall back to a mock provider when credentials are unset |
| TAS CRM/Calendar/Feature store | Real endpoints not wired | `app/integrations/crm.py`, `calendar.py`, `feature_store.py` call `*_SERVICE_URL` when set, otherwise stub |
| PAS data warehouse integration | Attribution/reconciliation use in-memory finance SoT | `DW_*` env vars are documented but not wired to a real DW |
| CMS Spark batch disparity | `jobs/disparity_batch.py` needs real Spark cluster | `app/spark_batch.py` unit-tested but not run on Spark |
| FLS MLflow model registry | Model lineage uses in-memory artifact store | `MLFLOW_TRACKING_URI` / `MLFLOW_API_KEY` are in `.env.example` but not used |

## 5. Security, Auth & RBAC

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| Default dev key must be replaced | `change-me-dev-key` is rejected in non-local envs, but no vault integration exists | `API_KEY` is a single shared secret from `.env` |
| No OAuth/SSO | Production requires identity provider integration | `X-Role` header only; no JWT/OAuth middleware |
| Tenant/branch isolation is partial | `tenant_id`/`branch_id` fields exist but isolation/SSO is P4 | `app/rbac.py` is role/scope helpers only |
| CORS origins unrestricted | `CORS_ORIGINS=*` or unset in local dev | `CORS_ORIGINS` must be restricted to production web origin |
| No audit log tamper monitoring in prod | Merkle anchoring and cold-store purge are in code but not wired to S3 | `WORM_GATEWAY=local` / `COLD_STORE_GATEWAY=local` |

## 6. Gateway & Cross-Agent

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| Limited proxy integration tests | Only `/health` and `/agents` root tests exist; no 502/404/proxy forwarding/auth pass-through/file download tests | `tests/integration/test_gateway.py` has 9 tests, mostly health and request-id |
| No end-to-end cross-agent tests | FUS → CMS, TAS → FUS, PAS → FLS flows not integrated in CI | `test_cross_agent.py` mocks `httpx.Client`; no container-to-container tests |
| Gateway timeout not configurable per agent | All agents share a single timeout | `settings.agent_timeout(agent)` exists but no per-route override |

## 7. Load, Resilience & Observability

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| No load test to 15k/s | P4 hardening target is p95 ≤ 3s at 15k events/s | `test_load_smoke_burst` is a small smoke test only |
| No retry/circuit-breaker policies | Cross-agent and third-party HTTP calls can fail silently | Clients use `httpx` with no retry/circuit breaker |
| OpenTelemetry not enabled by default | Traces/metrics are no-op unless `TRACING_ENABLED=true` | Collector in compose `--profile infra` but not in default stack |
| Prometheus/Grafana dashboards | No production dashboards | Provisioning files exist but no real workload validation |
| DLQ replay UI | Replay API exists; admin UI is P4 | `POST /api/v1/vision/dlq/{id}/replay` only |

## 8. Operational Readiness

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| No production runbooks | Incident response, scaling, rollback procedures missing | Only architecture/implementation docs exist |
| SOC 2 / fair-lending compliance runbooks | Compliance evidence procedures not documented | Guardrails and ledger in place; runbooks are P4 |
| No staged canary/shadow analytics dashboard | `canary_stats` is API-only | Dashboards deferred to GA Launch |
| Backup/DR strategy for Postgres/Redis/Kafka | Persistence defaults to memory; no backup plan | Not documented |

## 9. Type / Tooling Debt

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| mypy `NoDecode` warnings | Spurious type errors cascade; may indicate version mismatch | All agents import `NoDecode` from `pydantic_settings`; mypy may still flag it in CI depending on version |
| No mypy gate in CI | Type errors can reach production | CI runs `ruff` but not `mypy` |

## 10. Deployment & Packaging

| Blocker | Why it blocks P4 | Current State |
|---------|------------------|---------------|
| Docker Compose is local dev only | No Kubernetes manifests, autoscaling, or secrets management | Only `docker-compose.yml` with `--profile infra` |
| AWS/GCP/Azure deployment docs | Only CMS has a `deploy/README.md`; other agents do not | `src/agents/follow_up_sequences/deploy/README.md` and CMS deploy doc exist |
| Vault integration for secrets | Keys and DB credentials in `.env` | `AWS_*` / `SENDGRID_API_KEY` / `TWILIO_*` are plain env vars |

---

## Recommended P4 Roadmap Order

1. **Persistence:** enable Postgres for all five agents; run migrations; switch tests to Postgres or add a Postgres test stage.
2. **Redis:** enable `CACHE_GATEWAY=redis` and `REDIS_URL` in CI; add Redis integration tests.
3. **Kafka/Redpanda:** enable producers and consumers; validate partition-keyed per-deal ordering; add end-to-end cross-agent tests.
4. **Temporal:** deploy Temporal cluster; enable `TEMPORAL_ENABLED` for TAS doc workflows and FUS durable timers.
5. **External services:** obtain real credentials for Twilio, SendGrid, WhatsApp Business, CRM, calendar, feature store, DW, S3, MLflow; add contract/integration tests.
6. **Auth/RBAC:** implement OAuth/JWT middleware and tenant/branch enforcement.
7. **Load/resilience:** add retry/circuit-breaker, run 15k/s load test, enable OpenTelemetry and dashboards.
8. **Operational docs:** write runbooks, SOC 2 evidence, canary/shadow dashboards, and backup/DR plans.
9. **Deployment:** add Kubernetes manifests, vault/secret management, and multi-cloud packaging.

---

## See Also

- `docs/RemainingWork.md` — full remaining implementation checklist.
- `docs/DEPLOYMENT_READINESS.md` — per-agent readiness and production blockers.
- `docs/implementation_status_*.md` — P0–P3 + P4 status for each agent.
- `docs/ENGINE_TESTING_RESULTS.md` — latest test results.
