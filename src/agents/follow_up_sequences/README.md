# Follow-up Sequences Engine (FSE)

FastAPI service for the **engagement-persistence / cadence-orchestration** layer of the CRequity CRM
(Engine 2). It runs multi-step, multi-channel follow-up sequences (drip cadences) for borrowers,
brokers, and partners — with branching, intelligent stop conditions, and **compliance by
construction**: no message sends without passing a fail-closed guardrail (consent + DNC + TCPA
quiet-hours + frequency-cap). Every send is written to an auditable outreach ledger with its consent
basis.

The domain is built to the pasted spec (memory-backed; Temporal/Kafka/Redis/real-providers wire in
via the gateways):

- **Sequence builder** (FR) — versioned `SequenceDef`s of steps (channel + ISO-8601 delay + template +
  branches + A/B variants) and stop rules; multi-channel (SMS/email/WhatsApp/voicemail/call-task).
- **Enrollment** — manual, `lead.scored` ≥ threshold, tag-applied, or stage-change triggers;
  idempotent (one active per contact+sequence); a durable per-contact state machine.
- **Guardrail Gateway (fail-closed)** — the single choke point: opt-out/DNC → **stop**; unknown
  consent / manual-cooldown / **TCPA quiet-hours (contact-local)** / **global frequency-cap** → **hold**
  (reschedule); all clear → **send**. A missing consent basis can never produce a violation.
- **Scheduler** — `next_send_at = clamp_to_legal(best-time-snap(now + delay))`; the guardrail re-checks
  at send as the hard stop.
- **Channel orchestration** — one `deliver` interface with retry; a mock provider records sends
  (real Twilio/email/WhatsApp injected via `set_provider`).
- **Personalization** — `{{lead_name}}` / `{{property_name}}` / `{{agent_name}}` substitution (unknown
  vars render blank, never leaked).
- **Branching** — `opened → Path A`, `no_open_after:PT48H → Path B`, etc. (immediate signals branch at
  once; timed-negatives wait their window).
- **Stop conditions** — reply → **pause**; apply/convert → **stop**; opt-out → **stop + permanent
  suppression** (blocks re-enrollment).
- **A/B**, **per-step analytics** (delivery/open/reply rates), and an append-only **outreach ledger**.
- Publishes `sequence.enrolled` / `outreach.sent` / `outreach.failed` / `sequence.stopped` /
  `sequence.completed` (FR/§6).

## Latest P4 Hardening

- Bug-audit fixes: per-sequence frequency caps, Twilio/SendGrid/WhatsApp Business providers with mock fallback, 5-minute pre-send idempotency guard, `Literal` state validation, `Field(default_factory=dict)` for mutable defaults.
- `SuppressionEvent` + `POST /api/v1/webhooks/suppression` permanently suppress contacts and stop active enrollments.
- Repository methods, in-process event log, notification log, mock provider send log, and `FrequencyStore` counters are protected with `threading.RLock()`.

> The `/scheduler/tick` endpoint is the in-process stand-in for **Temporal** durable timers — it
> advances every due enrollment. Temporal, Kafka, Redis frequency counters, and real channel
> providers are deferred infra behind these interfaces.

## Stack
- FastAPI + uvicorn, pydantic-settings, structlog
- Optional tiers (lazy, degrade gracefully): Postgres (`[postgres]`), Redis (`[redis]`),
  Kafka (`[kafka]`), OpenTelemetry (`[otel]`)
- Tooling: **uv**, ruff (E,F,I,UP; line-length 100), pytest

## Run locally
```bash
uv sync
uv run pytest -q         # 50 passed, 1 warning
make run                 # API on http://127.0.0.1:8008 (reload)
```
Open **http://127.0.0.1:8008/docs**. Health: `/api/v1/health`, readiness: `/api/v1/ready`.

## Docker (local stack)
```bash
docker compose up --build   # API on :8008, Postgres on :5440, Redis on :6387
```

## API (all under `/api/v1`)
| Area | Endpoints |
|------|-----------|
| Definitions | `POST /sequences` · `GET /sequences/{id}` · `POST /contacts` |
| Enrollment | `POST /enrollments` · `GET /enrollments/{id}` · `GET /enrollments` · `POST /enrollments/{id}/state` |
| Scheduler | `POST /scheduler/tick` (Temporal-timer stand-in) |
| Signals | `POST /webhooks/engagement` · `POST /stage-changed` · `POST /contacts/{id}/opt-out` · `.../consent` · `.../manual-outreach` |
| Triggers | `POST /triggers/lead-scored` · `POST /triggers/tag-applied` |
| Reporting | `GET /analytics/sequences/{id}` · `GET /ledger` · `GET /events` · `GET /events/dlq` |

Write/ops endpoints require `X-API-Key`.

## Layout
```
app/
  main.py            # application factory (+ module-level `app`)
  core/config.py     # pydantic-settings (§7 cadence/compliance params)
  domain/models.py   # SequenceDef, Step, Enrollment, Contact, OutreachRecord, EngagementEvent
  constants.py       # channels / states / consent bases / stop reasons / guardrail blocks
  duration.py        # ISO-8601 duration + timed-branch-condition parsing
  quiet_hours.py     # TCPA quiet-hours (contact-local tz) + clamp-forward
  frequency.py       # global day/week frequency-cap counters
  guardrails.py      # FAIL-CLOSED gateway (consent/DNC/quiet-hours/freq-cap/cooldown)
  personalize.py     # {{variable}} substitution
  providers.py       # channel provider abstraction + retry (mock default)
  branching.py       # branch condition evaluation + timed-wait
  scheduler.py       # next_send_at (delay + best-time snap + legal-window clamp)
  abtest.py          # deterministic A/B variant allocation
  analytics.py       # per-step delivery/open/reply rates
  repositories.py    # sequences/contacts/enrollments/ledger/engagement (tenant-keyed, memory)
  events.py          # consumed triggers + produced outreach signals + DLQ
  service.py         # orchestration: enroll→tick(guardrail→send→branch)→stop; consent/opt-out
  api/sequences.py   # REST router
tests/               # guardrails, pipeline (ACs), api
deploy/              # ECS Fargate task definition + notes
Dockerfile · docker-compose.yml · Makefile · .env.example
```

## Configuration
Env-driven (see [`.env.example`](.env.example)) — `STORAGE_GATEWAY` (memory|postgres), `CACHE_GATEWAY`
(memory|redis), `EVENT_SOURCE_GATEWAY` (local|http|kafka), the §7 params (`QUIET_HOURS_*`,
`FREQ_CAP_DAY/WEEK`, `MANUAL_OUTREACH_COOLDOWN_H`, `DEFAULT_TIMEZONE`, `LEAD_SCORE_THRESHOLD`), and
`API_KEY`.

## Ports (across CREquity engines)
market-opportunity 8002 · personalized-alerts 8003 · compliance-monitoring 8004 · lead-scoring 8005 ·
sentiment-analytics 8006 · engagement-pattern-analytics 8007 · **followup-sequence 8008** ·
competitor-monitoring 8010 · market-positioning 8021.
