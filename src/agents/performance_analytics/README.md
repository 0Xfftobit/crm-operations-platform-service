# Performance Analytics System (PAS)

FastAPI service for the **measurement and accountability layer** of the CRM AI Assistant (Engine 2). PAS provides governed KPIs, multi-touch attribution, pipeline funnels, rep scorecards, channel effectiveness, and a KPI API so the platform can answer "is it working?" for loan officers, teams, campaigns, and every Engine 2 sub-agent.

## Implemented Scope

P0–P3 of the PAS ticket, plus the ROI Attribution and Channel Effectiveness subtasks:

- **P0 Ingestion**: touch, outcome, campaign cost, and finance source-of-truth ingestion with idempotent dedup (FR-001).
- **P1 Semantic Layer**: governed, versioned metric definitions (FR-003) and a KPI query API (FR-011).
- **P2 Marts**: funnel/velocity, rep/team scorecards, cohorts, channel effectiveness, reconciliation vs finance, anomaly/threshold alerts (FR-004..FR-007, FR-012..FR-014).
- **P3 Attribution**: first-touch, last-touch, linear/multi-touch, time-decay, Markov removal-effect, and Shapley-value attribution (FR-008) with ranked attribution dashboards and campaign/channel/sequence ROI (FR-009).
- **ROI Attribution**: first/last/linear/time-decay/Markov/Shapley models; attributed revenue and ROI per campaign/sequence/source; configurable attribution window.
- **Channel Effectiveness**: delivery, open, click, reply, opt-out, and conversion-contribution metrics; segment/stage/time-of-day breakdowns; automated channel recommendations.
- **Export & Reporting**: CSV and PDF export for metrics, funnels, and channel reports; scheduled report entity (FR-014).
- **A/B Uplift**: treatment vs control conversion lift with two-proportion z-test and p-value (FR-007).

## Latest P4 Hardening

- Bug-audit fixes: ISO-8601 reconciliation windows (`app/reconciliation.py` + `app/period_utils.py`), time-decay attribution uses the actual conversion `funded_at` (`app/attribution.py`), `typing.List` annotation fix, duplicate branch removal in `build_channel_effectiveness`, `_in_period_or_skip` for missing timestamps, and `threading.RLock()` on every in-memory repository.
- `tests/test_bug_audit.py` adds 7 regression tests; the suite is now `120/120` passing.
- Resilience: `ResilientClient` (exponential backoff, `X-Request-ID` propagation, `X-API-Key` forwarding) and `CircuitBreaker` for cross-agent fan-out to the Feedback Loop.
- Observability: Prometheus-compatible `/api/v1/metrics` endpoint, request-ID middleware, in-memory metrics fallback, and health/readiness probes (`/api/v1/health`, `/api/v1/ready`).
- PostgreSQL persistence: `STORAGE_GATEWAY=postgres` + `DATABASE_URL` with SQLAlchemy ORM, SQL repositories, and Alembic migrations; default remains `memory`; SQLite smoke tests pass.

## Architecture

```
┌──────────────┐     ┌─────────────────────┐     ┌─────────────────┐
│  Upstream    │────▶│   PAS API           │────▶│  Metric Store   │
│  engines     │     │  (FastAPI)          │     │  (in-memory or  │
│  / CRM       │     │                     │     │   DW/Postgres)  │
└──────────────┘     └──────────┬──────────┘     └─────────────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
 ┌──────────────┐      ┌──────────────┐      ┌─────────────────┐
 │ Ingestion    │      │ Dashboard    │      │ Attribution     │
 │ (touch/      │      │ Builder      │      │ Engine          │
 │  outcome)    │      │ (funnel,     │      │ (rule + data-   │
 └──────────────┘      │  scorecards, │      │  driven models) │
                       │  channel)    │      └─────────────────┘
                       └──────────────┘               │
                               │                      ▼
                               ▼               ┌─────────────────┐
                        ┌──────────────┐      │ ROI / Campaign  │
                        │ Reconciliation│      │ Reports         │
                        │ Anomaly      │      └─────────────────┘
                        │ Detection    │
                        └──────────────┘
```

## Data Flow

1. Upstream engines and the CRM send `touch`, `outcome`, and `campaign-cost` events to `POST /api/v1/ingest/*`.
2. PAS conflates events into deal-level rows and lands them in the repository.
3. The governed metric layer (`app/semantic.py`) defines versioned KPIs.
4. `DashboardBuilder` computes funnels, velocity, scorecards, channel effectiveness, and cohorts.
5. `Attribution` joins touches to funded deals within the lookback window and assigns credit under a selectable model.
6. `Reconciliation` compares `funded_amount` to finance SoT and emits alerts when tolerance is breached.
7. `AnomalyDetector` flags significant z-score deviations in core KPIs.
8. `Report` endpoints export CSV or PDF for dashboards and metrics.

## Data Model

The repository stores the following silver entities:

| Entity | Key fields | Purpose |
|--------|-----------|---------|
| `TouchEvent` | `touch_id`, `contact_pid`, `deal_id`, `rep_id`, `team_id`, `channel`, `campaign_id`, `sequence_id`, `source`, `sent_at`, `engaged`, `opened`, `clicked`, `replied`, `opted_out`, `delivery_status` | Every dispatched or tracked customer interaction |
| `OutcomeEvent` | `outcome_id`, `contact_pid`, `deal_id`, `stage`, `amount`, `occurred_at`, `event_type` | Funnel/funded/lost stage transitions and revenue |
| `CampaignCost` | `campaign_id`, `period`, `cost`, `currency` | Spend data for ROI calculations |
| `FinanceSourceOfTruth` | `period`, `funded_amount` | Engine 4 / finance source-of-truth for reconciliation |
| `MetricDefinition` | `name`, `version`, `label`, `metric_type`, `numerator`, `denominator`, `owner`, `dimensions` | Governed, versioned KPI catalog |
| `AttributionRecord` | `deal_id`, `model`, `funded_amount`, `credits` | Per-deal, per-model credit allocation |
| `ReportSchedule` | `report_id`, `name`, `metric`, `period`, `report_format`, `recipients`, `frequency` | Scheduled report metadata |

## Built-in Governed KPIs

The semantic layer is seeded with the following metrics on startup:

| Metric | Type | Description |
|--------|------|-------------|
| `lead_to_app_conversion` | ratio | Applications / leads |
| `app_to_fund_conversion` | ratio | Funded deals / applications |
| `funded_amount` | count | Sum of funded loan amounts |
| `deal_count` | count | Count of funded deals |
| `conversion_rate` | ratio | Funded deals / leads |
| `response_rate` | rate | Replied / delivered touches |
| `engagement_rate` | rate | Engaged / delivered touches |
| `open_rate` | rate | Opened / delivered touches |
| `click_rate` | rate | Clicked / delivered touches |
| `reply_rate` | rate | Replied / delivered touches |
| `opt_out_rate` | rate | Opted-out / delivered touches |
| `cost_per_funded_loan` | average | Campaign spend / funded deals attributed |
| `roi_per_campaign` | ratio | `(attributed revenue - cost) / cost × 100` |
| `median_cycle_time` | average | Median days from lead to funded |
| `pipeline_velocity` | average | Average days per funnel stage transition |

New metrics can be published through `POST /api/v1/metrics/definitions`; each new version increments the `version` field and marks the previous version as `superseded`.

## Attribution Models

| Model | Description |
|-------|-------------|
| `first_touch` | 100% credit to the first touch in the lookback window |
| `last_touch` | 100% credit to the last touch before conversion |
| `linear` | Equal credit split across all touches |
| `time_decay` | Exponentially more credit to touches closer to conversion (configurable half-life) |
| `markov` | Removal-effect approximation using empirical channel conversion rates |
| `shapley` | Shapley value over the set of channels touched for a given deal |

All model credits for a funded deal sum to `1.0`. Use `?model=<name>` on the attribution endpoints.

## Period Expressions

Most dashboard and KPI endpoints accept a `period` parameter. Supported values:

- **Bucket keywords**: `daily`, `weekly`, `monthly`, `quarterly`, `yearly` (groups all data by that grain).
- **Specific buckets**: `2026-08-06`, `2026-W32`, `2026-08`, `2026-Q3`, `2026`.
- **Custom ranges**: `2026-08-01/2026-08-31` or `2026-08-01T00:00:00+00:00/2026-08-31T23:59:59+00:00`.

When `period` is a bucket keyword and `dim=period` is requested, the KPI is grouped by the corresponding time buckets.

## API Reference

All routes are under `/api/v1`. The API gateway proxies `/performance-analytics/*` to this service.

### Ingestion

| Method | Path | Summary |
|--------|------|---------|
| `POST` | `/ingest/touch` | Ingest a touch event |
| `POST` | `/ingest/outcome` | Ingest a funnel/funded outcome |
| `POST` | `/ingest/campaign-cost` | Ingest campaign spend |
| `POST` | `/ingest/finance-source` | Record finance source-of-truth |

### Governed Metrics & KPIs

| Method | Path | Summary |
|--------|------|---------|
| `GET`  | `/metrics/definitions` | List versioned KPI definitions |
| `GET`  | `/metrics/definitions/{name}` | Get a metric definition |
| `GET`  | `/metrics/definitions/{name}/history` | Metric version history |
| `GET`  | `/metrics/definitions/{name}/lineage` | Upstream metric lineage |
| `POST` | `/metrics/definitions` | Publish a new metric version |
| `GET`  | `/explore` | Self-serve metric/dimension exploration |
| `GET`  | `/kpi/{metric_name}` | Query a governed metric |

### Dashboards

| Method | Path | Summary |
|--------|------|---------|
| `GET`  | `/funnel` | Pipeline funnel + cycle times |
| `GET`  | `/scorecards/reps` | Rep scorecards |
| `GET`  | `/scorecards/reps/{rep_id}` | Single rep scorecard |
| `GET`  | `/leaderboard` | Rep leaderboard |
| `GET`  | `/channel-effectiveness` | Channel metrics |
| `GET`  | `/channel-heatmaps` | Time-of-day heatmaps |
| `GET`  | `/channel-recommendations` | Automated recommendations |
| `GET`  | `/cohorts` | Cohort conversion analysis |

### Attribution

| Method | Path | Summary |
|--------|------|---------|
| `GET`  | `/attribution` | Touch-level credit for a funded deal |
| `POST` | `/attribution/compute` | Compute attribution for all funded deals |
| `GET`  | `/attribution/summary` | Ranked attribution summary |
| `GET`  | `/attribution/entities` | Ranked attributed revenue by entity (channel, campaign_id, rep_id, etc.) |
| `GET`  | `/attribution/export` | Export attributed revenue by entity as CSV or PDF |

### Reconciliation, Alerts, A/B

| Method | Path | Summary |
|--------|------|---------|
| `POST` | `/reconcile/{period}` | Reconcile vs finance SoT |
| `GET`  | `/reconciliations` | Reconciliation history |
| `POST` | `/detect-anomalies/{metric}` | Run anomaly detection |
| `POST` | `/threshold-alert/{metric}` | Set threshold alert |
| `GET`  | `/alerts` | List alerts |
| `POST` | `/ab-uplift` | Treatment vs control uplift |

### Reports & Export

| Method | Path | Summary |
|--------|------|---------|
| `POST` | `/reports/schedule` | Schedule a report |
| `GET`  | `/reports` | List scheduled reports |
| `GET`  | `/reports/{report_id}/run` | Run a report (CSV/PDF) |
| `GET`  | `/export/{metric_name}` | Export a metric (CSV/PDF) |

## Detailed Examples

### Ingest a touch and a funded outcome

```bash
curl -H "X-API-Key: change-me-dev-key" \
  -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8007/api/v1/ingest/touch \
  -d '{
    "touch_id": "t-1",
    "contact_pid": "c-1",
    "deal_id": "d-1",
    "rep_id": "r-1",
    "team_id": "team-a",
    "channel": "email",
    "campaign_id": "camp-1",
    "sent_at": "2026-06-01T12:00:00+00:00",
    "opened": true
  }'

curl -H "X-API-Key: change-me-dev-key" \
  -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8007/api/v1/ingest/outcome \
  -d '{
    "outcome_id": "o-1",
    "contact_pid": "c-1",
    "deal_id": "d-1",
    "rep_id": "r-1",
    "team_id": "team-a",
    "stage": "funded",
    "amount": 300000,
    "event_type": "loan.funded",
    "occurred_at": "2026-06-15T12:00:00+00:00"
  }'
```

### Query the funnel

```bash
curl -X GET "http://127.0.0.1:8007/api/v1/funnel?period=2026-06"
```

Response:

```json
{
  "stages": [
    {"stage": "lead",   "count": 10, "median_cycle_days": null},
    {"stage": "app",    "count": 7,  "median_cycle_days": 1.5,  "conversion_from_previous": 0.7},
    {"stage": "approved", "count": 5, "median_cycle_days": 3.0,  "conversion_from_previous": 0.71},
    {"stage": "funded", "count": 4,  "median_cycle_days": 10.5, "conversion_from_previous": 0.8}
  ],
  "period": "2026-06"
}
```

### Explore available metrics and dimensions

```bash
curl -X GET "http://127.0.0.1:8007/api/v1/explore"
```

### View metric version history and lineage

```bash
curl -X GET "http://127.0.0.1:8007/api/v1/metrics/definitions/deal_count/history"

curl -X GET "http://127.0.0.1:8007/api/v1/metrics/definitions/deal_count/lineage"
```

### Query a KPI with dimensions and filters

```bash
curl -X GET "http://127.0.0.1:8007/api/v1/kpi/app_to_fund_conversion?dim=channel&period=2026-06"
```

### Get attribution for a funded deal

```bash
curl -X GET "http://127.0.0.1:8007/api/v1/attribution?deal_id=d-1&model=time_decay"
```

### Rank attributed revenue by channel or campaign

```bash
curl -X GET "http://127.0.0.1:8007/api/v1/attribution/entities?entity=channel&period=2026-06"

curl -X GET "http://127.0.0.1:8007/api/v1/attribution/entities?entity=campaign_id&model=linear"
```

### Export attributed revenue as CSV or PDF

```bash
curl -X GET "http://127.0.0.1:8007/api/v1/attribution/export?entity=channel&report_format=csv&period=2026-06" \
  -o attribution_channel.csv

curl -X GET "http://127.0.0.1:8007/api/v1/attribution/export?entity=campaign_id&report_format=pdf" \
  -o attribution_campaign.pdf
```

### Run an A/B uplift test

```bash
curl -H "X-API-Key: change-me-dev-key" \
  -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8007/api/v1/ab-uplift \
  -d '{
    "treatment": ["d-1", "d-2"],
    "control": ["d-3", "d-4"],
    "stage": "funded"
  }'
```

### Export a metric as PDF

```bash
curl -X GET "http://127.0.0.1:8007/api/v1/export/deal_count?report_format=pdf&period=2026-06" \
  -o deal_count.pdf
```

## Authentication & Row-Level Security

- **Write/ops endpoints** require the `X-API-Key` header with the value configured in `API_KEY`.
- **RLS context** is read from these headers when present:
  - `X-Tenant-Id`
  - `X-Team-Id`
  - `X-Rep-Id`
  - `X-Role`

These values are applied as query filters automatically, so an agent or manager only sees data for their tenant/team/rep. A full policy engine/SSO integration is planned for P4.

## Configuration

Copy `.env.example` to `.env` and adjust as needed. Key variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `APP_NAME` | `crequity-performance-analytics` | Service name |
| `ENV` | `local` | Environment label |
| `LOG_LEVEL` | `INFO` | structlog level |
| `LOG_JSON` | `false` | JSON vs console logs |
| `API_KEY` | `change-me-dev-key` | Key for `X-API-Key` header on write endpoints |
| `API_PREFIX` | `/api/v1` | Route prefix |
| `STORAGE_GATEWAY` | `memory` | `memory` (default) or `postgres` (requires `DATABASE_URL`) |
| `STORAGE_BACKEND` | `memory` | `memory` (default) or `disk` for snapshots |
| `DATABASE_URL` | (empty) | PostgreSQL+psycopg URL when `STORAGE_GATEWAY=postgres` |
| `DB_AUTO_CREATE` | `false` | Create SQL tables on startup (dev only; prod uses `alembic upgrade head`) |
| `DATA_DIR` | `./data/performance_analytics` | Local path for `disk` snapshots |
| `ATTRIBUTION_LOOKBACK_D` | `90` | Attribution window |
| `ATTRIBUTION_DEFAULT_MODEL` | `time_decay` | Default credit model |
| `TIME_DECAY_HALFLIFE_D` | `14` | Time-decay half-life |
| `RECON_TOLERANCE` | `0.005` | Reconciliation delta tolerance |
| `COHORT_MIN_N` | `25` | Suppress cohorts below this size |
| `ANOMALY_Z_THRESHOLD` | `2.0` | Anomaly z-score threshold |

## Running Locally

```bash
cd src/agents/performance_analytics
uv sync
uv run pytest -q
make run                 # API on http://127.0.0.1:8007 (reload)
```

Open **http://127.0.0.1:8007/docs** for the interactive OpenAPI UI.

Health: `/api/v1/health`, readiness: `/api/v1/ready`, Prometheus metrics: `/api/v1/metrics`.

## Testing

The agent has an isolated pytest suite under `tests/`:

```bash
cd src/agents/performance_analytics
uv run pytest tests -q          # 120 passed, 1 warning
uv run pytest tests -v          # per-test verbose output
```

Linting and formatting use `ruff`:

```bash
make lint      # ruff check app tests
make fmt       # ruff format app tests
```

For more detail, see `docs/testing.md`.

## Key Modules

- `app/metrics_collector.py` — touch/outcome/cost/finance ingestion with dedup (P0).
- `app/semantic.py` — governed, versioned metric definitions (P1).
- `app/dashboard_builder.py` — funnel, scorecards, channel effectiveness, cohorts, A/B uplift (P2).
- `app/attribution.py` — multi-touch attribution models and ROI (P3).
- `app/reconciliation.py` — finance source-of-truth reconciliation.
- `app/anomaly_detector.py` — z-score anomaly and threshold alerts.
- `app/pdf.py` — minimal stdlib CSV + PDF export.
- `app/service.py` — orchestration layer used by API routes.
- `app/api/analytics.py` — REST routes.
- `app/repositories.py` — in-memory repository implementations with SQLAlchemy/SQLite/Postgres dispatch.

## Roadmap

1. Validate live PostgreSQL integration in staging/production (Postgres SQL persistence is code-complete; SQLite smoke tests pass).
2. Add object/S3 storage for exported CSV/PDF artifacts.
3. Implement true dbt-style semantic layer and metric lineage.
4. Replace simplified Markov/Shapley with production-grade channel attribution.
5. Add BI/dashboard rendering (Looker/Superset/Metabase integration).
6. Add scheduled report delivery (email/Slack) and production cron.
7. Implement full row-level security with SSO and tenant policies.
8. Add streaming KPI marts with Flink/Spark and freshness monitoring.
