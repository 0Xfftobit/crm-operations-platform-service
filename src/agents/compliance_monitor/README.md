# Compliance Monitoring Engine

FastAPI service for the Compliance Monitoring engine — **Engine 8**, the platform's audit + governance
layer. It screens every CRM activity, AI decision, and outbound communication for **TCPA / CAN-SPAM /
ECOA / fair-lending / GDPR-CCPA** adherence, keeps an **immutable, hash-chained audit trail**, and
raises tiered alerts (critical → auto-case) with Engine-8 signals for correlation.

Three integrated sub-systems (memory-backed; Postgres/Redis/Kafka tiers wire in via the gateways):

- **Compliance Monitor** — fail-closed pre-send hook (TCPA quiet-hours, opt-out, DNC, CAN-SPAM,
  high-risk PII), fair-lending disparity monitor (4/5ths + min-sample) with batch geographic /
  redlining detection, ECOA adverse-action clocks (30-day, T-7d/T-2d warnings), PII exposure
  scanning, explainability enforcement, data-retention + GDPR right-to-erasure, Engine-8 policy
  ingestion, tiered alerts/cases (with dedup), and the RAG dashboard.
- **Regulatory Updates** — rule versioning + **two-step approval** (proposer ≠ approver) +
  staging→production deploy + emergency override + regulation tracker + change-monitoring alerts.
- **Audit Trail** — append-only hash-chained ledger, search/filter, on-demand + periodic tamper
  verification, RBAC **CSV / JSON / PDF** export, 7-year retention.

## Latest P4 Hardening

- Bug-audit fixes: `coverage_sweep_interval_min`, `http_source.poll_once` non-numeric handling, `CaseUpdate.status`/`service._raise` `Literal`/typed, exact-year retention, float `days_left`, `coldstore.py` `encoding="utf-8"` and S3 range pagination.
- In-memory repositories and shared state are protected with `threading.RLock()`.
- Resilience: `ResilientClient` (exponential backoff, `X-Request-ID` propagation, `X-API-Key` forwarding) and `CircuitBreaker` on HTTP-poll upstream and third-party calls.
- Observability: Prometheus-compatible `/api/v1/metrics` endpoint, request-ID middleware, in-memory metrics fallback, and health/readiness probes (`/api/v1/health`, `/api/v1/ready`) that report `storage`/`postgres`/`redis`/`kafka` status.
- Kafka contract bus: producer/consumer in `app/kafka_bus.py` with canonical topics and in-memory fallback.

> The protected attributes used by the disparity monitor are for **monitoring only** — never a
> decision input (BR-2). Every state-changing path is written to the immutable ledger first.

## Stack
- FastAPI + uvicorn, pydantic-settings, structlog
- SQLAlchemy + Alembic + psycopg (Postgres) — wired via `STORAGE_GATEWAY`; `memory` default
- Optional tiers (lazy, degrade gracefully): Redis hot-cache (`[redis]`), Kafka bus (`[kafka]`,
  aiokafka), S3 WORM (`[worm-s3]`, boto3), Spark batch (`[spark]`), OpenTelemetry tracing (`[otel]`)
- Tooling: **uv** (`uv sync`, `uv run …`), ruff (E,F,I,UP; line-length 100), pytest

## Durability & infrastructure tiers
All default to in-memory and are opt-in via env — the memory path is fully functional, so nothing
below is required to run or test the domain.
- **Durable SQL** (`STORAGE_GATEWAY=postgres` + `DATABASE_URL`): SQLAlchemy repositories + a
  columnar, hash-chained `audit_ledger`. Dialect comes from the URL (Postgres in prod, SQLite in
  tests). Schema via `alembic upgrade head` (or `DB_AUTO_CREATE=true` for dev).
- **Redis hot-cache** (`CACHE_GATEWAY=redis`): short-TTL consent cache on the fast pre-send path;
  a miss falls through to the durable repo, so correctness never depends on the cache.
- **WORM + Merkle anchoring** (`WORM_GATEWAY=local|s3`): every entry mirrored to a write-once object
  (local read-only file, or **S3 Object Lock COMPLIANCE**); periodic Merkle roots anchored for
  compact tamper-evidence with inclusion proofs (`GET /audit/{seq}/proof`).
- **Audit lifecycle** (`COLD_STORE_GATEWAY=local|s3`): aged entries tier to **cold storage** after
  `AUDIT_COLD_AGE_DAYS` (default 1y); past `AUDIT_RETENTION_YEARS` (default 7y) a contiguous prefix
  that is **cold-archived + Merkle-anchored** is **purged** from the hot ledger. The chain stays
  verifiable via a **carry-hash watermark** (`verify` resumes from it), and purged records remain
  retrievable from cold (`GET /audit/cold/{seq}`) with tamper-evidence via the retained anchor.
- **Kafka bus** (`KAFKA_PRODUCE` / `KAFKA_INGEST`, aiokafka): consumer streams activity events into
  `record_activity`; producer mirrors `compliance.alert/case/signal` onto the bus for Engine 8.
- **Spark batch** (`jobs/disparity_batch.py`, run via `spark-submit`): nightly redlining/geographic
  disparity at scale; the compliance logic is the unit-tested `app/spark_batch.py` core.
- **Coverage-gap monitoring** (BR-3): per-source `source_seq` gaps → dropped-event alerts, a
  heartbeat sweep for silent sources, and a dead-letter counter (`GET /coverage`).
- **HTTP-poll upstream** (`EVENT_SOURCE_GATEWAY=http` + `AUDIT_SOURCE_URL`): polls a REST endpoint
  for activity events when there is no Kafka bus (cursor-advanced, idempotent).
- **OpenTelemetry tracing** (`TRACING_ENABLED=true`, `[otel]`): auto-instruments every request with
  an OTLP/HTTP exporter; no-op when disabled or the libs are absent.

## Run locally
```bash
uv sync                 # create .venv + install
uv run pytest -q        # 179 passed, 1 warning
make run                # API on http://127.0.0.1:8004 (reload)
```
Open **http://127.0.0.1:8004/docs** for the interactive API. Health: `/api/v1/health`,
readiness: `/api/v1/ready`, Prometheus metrics: `/api/v1/metrics`, plus a bare `/health`.

## Docker (full stack)
```bash
docker compose up --build   # API :8004, Postgres :5436, Redis :6383, Redpanda (Kafka) :9092
```
The compose `app` runs the full wiring: Postgres persistence, Redis cache, Kafka produce+ingest,
local WORM volume, and hourly Merkle anchoring.

## AWS deployment
Multi-stage `uv` image → **ECR**, run on **ECS Fargate**, secrets in **Secrets Manager**, logs to
**CloudWatch**, health check on `/api/v1/health`. Steps + task definition in [`deploy/`](deploy/README.md).

## API (all under `/api/v1`)
| Area | Endpoints |
|------|-----------|
| Pre-send hook | `POST /precheck` (TCPA/CAN-SPAM/opt-out/DNC + high-risk PII) · `PUT /consent` · `GET /consent/{lead_id}` |
| Activity | `POST /activity` (ingest → audit + explainability/override/PII detectors) |
| Fair-lending | `POST /fairness/evaluate` · `POST /fairness/batch` (geographic / redlining) |
| Disclosures | `POST /disclosures` (any kind) · `POST /disclosures/stage` (start stage-required) · `GET /disclosures/requirements` · `POST /disclosures/{deal_id}/satisfy?kind=` · `GET /disclosures` · `GET /disclosures/check` |
| Coverage (BR-3) | `GET /coverage` · `POST /coverage/sweep` |
| Data privacy | `POST /privacy/retention` · `GET /privacy/retention` · `POST /privacy/retention/check` · `POST /privacy/erase` (right-to-erasure) |
| Explainability | `POST /explainability` · `GET /explainability/{ref}` |
| Engine-8 policy | `POST /policies` (signed, versioned) · `GET /policies/active` |
| Alerts/cases | `GET /alerts` · `GET /cases` · `PATCH /cases/{id}` |
| Dashboard/events | `GET /dashboard` (RAG) · `GET /events` (Engine-8 feed) |
| Audit trail | `GET /audit/search` · `GET /audit/{seq}` · `GET /audit/verify` · `POST /audit/integrity-check` · `GET /audit/export?fmt=csv\|json\|pdf` (RBAC via `X-Role`) |
| Tamper-evidence | `POST /audit/anchor` · `GET /audit/anchors` · `GET /audit/{seq}/proof` (Merkle) · `GET /audit/worm/{seq}` |
| Audit lifecycle | `GET /audit/lifecycle` · `POST /audit/cold-tier` · `POST /audit/purge` · `GET /audit/cold/{seq}` |
| Rules | `POST /rules/changes` · `.../approve` · `.../reject` · `.../deploy` · `POST /rules/emergency` · `GET /rules` · `GET /rules/{id}/history` |
| Regulations | `PUT /regulations` · `GET /regulations` · `POST /regulations/{id}/change` · `.../reviewed` · `POST /regulations/sweep` |

Write/ops endpoints require `X-API-Key`; audit export additionally requires a privileged `X-Role`.

## Layout
```
app/
  main.py            # application factory (+ module-level `app`)
  core/
    config.py        # pydantic-settings (env-driven; §7 compliance params; uses_sql/uses_cache)
    logging.py       # structlog (console dev / JSON prod)
  domain/models.py   # AuditEntry, Consent, Precheck, Disparity, DisclosureTimer, Rule, Regulation…
  audit.py           # append-only hash-chained ledger (append/search/verify/export)
  repositories.py    # in-memory repos (consent, alerts, cases, disclosures, rules, regs)
  precheck.py        # fail-closed TCPA/opt-out/DNC/CAN-SPAM gate
  fairness.py        # 4/5ths disparity + min-sample guard
  disclosures.py     # ECOA adverse-action clocks
  rules.py           # rule versioning + two-step approval + emergency override
  policies.py        # FR-014 Engine-8 policy → effective-settings overlay
  pii.py             # FR-007 PII pattern scan (content + payloads)
  privacy.py         # data-retention engine + GDPR/PDPA right-to-erasure
  regwatch.py        # FR CEAAPSS-565 regulatory-change monitoring + staleness sweep
  pdf.py             # dependency-free PDF audit export
  casework.py        # tiered alerts; critical → auto-case; FR-019 dedup
  worm.py            # WORM object-lock mirror (local write-once / S3 Object Lock)
  coldstore.py       # cold-storage archive (local / S3) for aged + purged records
  merkle.py          # Merkle tree, root, inclusion proofs
  cache.py           # consent hot-cache (memory / Redis)
  kafka_bus.py       # aiokafka producer + consumer loop
  http_source.py     # EVENT_SOURCE_GATEWAY=http poll loop
  coverage.py        # BR-3 sequence-gap + heartbeat + dead-letter monitoring
  spark_batch.py     # pure batch disparity aggregation (Spark job core)
  core/tracing.py    # OpenTelemetry auto-instrumentation (opt-in)
  events.py          # produced compliance.alert/case/signal feed (+ Kafka)
  service.py         # orchestration: ingest→audit→detect→alert/case; precheck; anchoring
  db/                # SQLAlchemy engine, ORM tables, SQL repos, ledger backend
  api/               # health, compliance, audit, rules routers + deps (X-API-Key / X-Role)
jobs/                # disparity_batch.py — spark-submit batch job
migrations/          # Alembic (0001 initial schema from ORM metadata)
tests/               # acceptance-mapped + infra: sql persistence, worm/merkle, kafka/spark/cache
deploy/              # ECS Fargate task definition + notes
Dockerfile · docker-compose.yml · Makefile · alembic.ini · .env.example
```

## Configuration
Everything is env-driven (see [`.env.example`](.env.example)) — `STORAGE_GATEWAY` (memory|postgres),
`CACHE_GATEWAY` (memory|redis), `EVENT_SOURCE_GATEWAY` (local|http|kafka) + `AUDIT_SOURCE_URL` for the
upstream audit stream, and `API_KEY` for write/ops endpoints.
