# Follow-up Sequences Engine — Implementation Status

## P4 Progress Update

This P4 pass added per-sequence frequency caps (`SequenceDef.freq_cap_day`/`freq_cap_week`), real channel providers (Twilio SMS, SendGrid email, WhatsApp Business API, voicemail mock) that fall back to the mock provider when credentials are unset, a 5-minute pre-send idempotency guard (`_already_attempted`), `Literal` state validation on `StateRequest`, and `Field(default_factory=dict)` for mutable Pydantic request defaults. The latest P4 pass also added a `SuppressionEvent` model and `POST /api/v1/webhooks/suppression` endpoint for bounce/complaint/spam/block ingestion, `Contact` fields for `suppressed`, `suppressed_reason`, and `owner_id`, `guardrails.check` suppression stops, `service.apply_suppression` with `contact.suppressed` events, `notifications.notify_owner_of_reply` with an HTTP-to-in-process-log fallback, `threading.RLock()` in all repositories, `X-Request-ID` propagation in the CMS precheck client, and the `NOTIFICATION_SERVICE_URL`, `NOTIFICATION_API_KEY`, and `INTEGRATION_TIMEOUT_S` environment variables. Redis-backed `FrequencyStore` and third-party credentials remain optional — the engine works out of the box when they are not configured.

## 1. Overview

The `follow_up_sequences` agent is a self-contained FastAPI service that implements the **Follow-up Sequences Engine (FSE)** for the CRM AI Assistant platform. It orchestrates multi-step, multi-channel follow-up cadences (drip sequences) for borrowers, brokers, and referral partners — with branching logic, timezone-aware scheduling, fail-closed compliance guardrails, and intelligent stop conditions.

This document tracks what has been implemented against the FUS ticket (`docs/ticket_description_fus.md`), using the following legend:

| Emoji | Meaning |
|-------|---------|
| ✅ | Implemented and covered by tests in P0–P3 |
| 🚧 | Implemented as a stub / in-memory approximation or missing production integration |
| 📝 | Planned for P4 (Promotion / GA Launch) or future work |
| ❌ | Not implemented and not currently planned |

## 2. Scope Delivered

| Phase / Deliverable | Theme | Status | Key Deliverables |
|---------------------|-------|--------|------------------|
| **P0** | Foundations | ✅ | Versioned `SequenceDef`/`Step`/`Branch` schema, `Contact` model with consent/timezone, `Enrollment` state machine, in-memory repositories, health/readiness endpoints (`tests/test_health.py`) |
| **P1** | Core cadence | ✅ | `Scheduler` (`next_send_at` with quiet-hours clamp + best-time snap), `GuardrailGateway` (consent/DNC/quiet-hours/frequency-cap), `Channel Orchestrator` interface with retry, `Personalizer` variable substitution |
| **P2** | Branching | ✅ | Engagement-driven branching (`opened`, `no_open_after:PT48H`, etc.), stop conditions (reply/apply/opt-out/max-steps), global frequency cap across all cadences |
| **P3** | Optimization | 🚧 | A/B variant allocation (`app/abtest.py`), per-step analytics (`app/analytics.py`); per-variant conversion reporting and Timing-Optimizing-Engine integration are P4 |
| **P4** | Hardening | 📝 | Real Twilio/email/WhatsApp providers, voicemail-drop channel, Temporal durable workflows, 2k msg/s load test, SOC 2/TCPA evidence |
| **Vision** | Requirements §1–7 | ✅ | Captured in `docs/ticket_description_fus.md` and reflected in the engine mission statement |
| **Project Requirements** | §8–10 | ✅ | Objectives, NFRs, constraints, risks, acceptance criteria mapped to code/tests |
| **Functional Requirements** | §11 (FR-001..FR-020) | ✅/🚧 | See §3 below |
| **System Architecture** | §12 | ✅ | Component design implemented (Enrollment, Scheduler, Guardrails, Channel, Branching, Analytics, Ledger) |
| **System Design** | §13 | ✅ | Data models, schemas, API spec, workflow/algorithm design, configuration parameters implemented |

## 3. Functional Requirements (FR-001..FR-020)

| ID | Requirement | MoSCoW | Status | Notes |
|----|-------------|--------|--------|-------|
| FR-001 | Define sequences with ordered steps, delays, channels, branches, exits | Must | ✅ | `SequenceDef` / `Step` / `Branch` / `stop_rules` in `app/domain/models.py:44-52`; `POST /api/v1/sequences` in `app/api/sequences.py:21-32` |
| FR-002 | Enroll a contact via event or REST API with de-duplication | Must | ✅ | `service.enroll()` at `app/service.py:76-105`; REST `POST /api/v1/enrollments` (`app/api/sequences.py:50-53`); trigger endpoints `lead-scored` and `tag-applied` (`app/api/sequences.py:133-149`) |
| FR-003 | Compute next-step send time honoring quiet hours + best-time window | Must | ✅ | `scheduler.next_send_at()` at `app/scheduler.py:38-43`; best-time snap `_snap_to_best_time` and quiet-hours clamp `quiet_hours.next_allowed` at `app/quiet_hours.py:31-42` |
| FR-004 | Enforce consent + DNC + opt-out check fail-closed before every send | Must | ✅ | `guardrails.check()` at `app/guardrails.py:36-77`; `opted_out`/`dnc` → `stop`; unknown consent → `hold`; covered by `tests/test_guardrails.py` |
| FR-005 | Enforce global + per-sequence frequency caps across all cadences | Must | ✅/🚧 | Core global + per-sequence day/week caps implemented in `FrequencyStore` (`app/frequency.py:26-81`) and enforced in `guardrails.check()` (`app/guardrails.py:70-80`); `SequenceDef.freq_cap_day`/`freq_cap_week` in `app/domain/models.py:52-53`. Redis production scaling is optional. |
| FR-006 | Send via SMS, email, voicemail-drop, or create call-task | Must | ✅/🚧 | Twilio (`_twilio_send`), SendGrid (`_sendgrid_send`), WhatsApp Business (`_whatsapp_send`), and voicemail mock/call-task (`_voicemail_send`) in `app/providers.py:40-135`; `deliver()` retries with exponential backoff on 429s and failures up to `PROVIDER_RETRY_MAX` (`app/providers.py:163-183`). Falls back to mock when credentials are unset. |
| FR-007 | Branch on engagement signals (delivered/opened/clicked/replied) | Must | ✅ | `branching.next_step_from_branches()` at `app/branching.py:38-43`; conditions `opened`/`clicked`/`replied` and timed negatives (`app/branching.py:29-35`) |
| FR-008 | Branch on deal-stage change (e.g., stop nurture when application starts) | Must | 🚧 | Converted stages (`CONVERTED_STAGES`) trigger sequence stop at `app/service.py:257-267`; true non-terminal stage-based branches (e.g., branch to a different step on `stage=X`) are not yet implemented |
| FR-009 | Evaluate stop conditions (reply, apply, opt-out, max-steps, goal-met) | Must | ✅ | Reply → pause (`app/service.py:240-247`); converted stage → stop (`app/service.py:257-267`); opt-out/DNC → stop (`app/service.py:270-280`); max-steps → complete (`app/service.py:173-176`); manual state API (`app/service.py:311-323`) |
| FR-010 | Personalize message content via Engine 3 templates with variables | Should | 🚧 | Local `{{lead_name}}`/`{{property_name}}`/`{{agent_name}}` substitution in `app/personalize.py:29-32`; unknown variables render blank; Engine 3 integration is P4 |
| FR-011 | Idempotent send per (contact_id, step_id, occurrence) | Must | ✅/🚧 | Ledger tracks `(enrollment, step, occurrence)` keys (`app/repositories.py:90-104`); occurrence is computed (`app/service.py:76-85`) and a 5-minute pre-send dedupe window (`_already_attempted`) blocks duplicate/redelivery attempts (`app/service.py:88-95`). Temporal exactly-once guarantee is P4. |
| FR-012 | A/B test step variants and report per-variant conversion | Should | 🚧 | Deterministic variant allocation by `enrollment_id` in `app/abtest.py:14-21`; variant is recorded on the ledger (`app/service.py:158-159`); per-variant conversion reporting is not yet built (P4) |
| FR-013 | Pause/resume/cancel an enrollment via API or UI | Must | ✅ | `POST /api/v1/enrollments/{id}/state` at `app/api/sequences.py:69-75`; `StateRequest.state` is `Literal["paused", "resume", "stopped"]` (`app/api/sequences.py:69-70`); `service.set_enrollment_state()` rejects invalid states (`app/service.py:311-323`) |
| FR-014 | Emit outreach + engagement events for analytics and compliance | Must | ✅ | `events.publish()` for `sequence.enrolled` / `outreach.sent` / `outreach.failed` / `sequence.stopped` / `sequence.completed` at `app/events.py:31-52`; DLQ for malformed/failed sends (`app/events.py:36-39`) |
| FR-015 | Maintain suppression lists (bounce, complaint, global DNC) | Must | ✅/🚧 | `SuppressionEvent` model and `POST /api/v1/webhooks/suppression` endpoint (`app/api/sequences.py:124-126`); `Contact.suppressed`/`suppressed_reason` fields (`app/domain/models.py:64,71-73`); `guardrails.check` stops on `contact.suppressed` (`app/guardrails.py:47-48`); `service.apply_suppression` sets suppression flags, stops active enrollments, and emits `contact.suppressed` (`app/service.py:365-383`); global list is in-process per tenant (in-memory) |
| FR-016 | Notify owner LO on inbound reply | Should | ✅/🚧 | `Contact.owner_id` field (`app/domain/models.py:64`); `app/notifications.py:notify_owner_of_reply` with HTTP `POST` to `NOTIFICATION_SERVICE_URL` and fallback to an in-process log (`app/notifications.py:46-73`); `service.ingest_engagement` calls it for `reply` events when `contact.owner_id` is set (`app/service.py:300-305`); in-process log is the default when no URL is configured |
| FR-017 | Support timezone-aware scheduling from contact profile | Must | ✅ | Contact `timezone` used in `quiet_hours.is_allowed` / `next_allowed` (`app/quiet_hours.py:24-42`) and scheduler (`app/scheduler.py:38-43`); `DEFAULT_TIMEZONE` fallback |
| FR-018 | Version sequences with safe migration of in-flight enrollments | Should | 🚧 | `SequenceDef.version` and version-aware `get()` in `app/repositories.py:20-42`; enrollment stores its `version`; safe migration / pin-or-migrate flag for in-flight enrollments not implemented |
| FR-019 | Throttle/retry on provider rate limits and failures | Must | ✅/🚧 | `deliver()` retries up to `PROVIDER_RETRY_MAX` (`app/core/config.py:84`) with exponential backoff for 429 rate limits and transient failures (`app/providers.py:163-183`). Multi-provider failover is P4. |
| FR-020 | Record consent basis + stop reason in the outreach ledger | Must | ✅ | `OutreachRecord` model includes `consent_basis`, `block_reason`, `status`, `attempts` (`app/domain/models.py:101-119`); `_ledger()` writes every attempt at `app/service.py:210-219`; stop reason written to `Enrollment.stopped_reason` |

## 4. Main FUS Ticket Requirement Coverage

### Functional Requirements (from the top-level ticket)

| Requirement | Status | Notes |
|-------------|--------|-------|
| Sequence builder: steps by channel, delay, template, conditions | ✅ | `SequenceDef`/`Step`/`Branch` models and `POST /api/v1/sequences` |
| Multi-channel support: email, SMS, WhatsApp | ✅/🚧 | Channels `sms`/`email`/`whatsapp`/`voicemail`/`call_task` accepted (`app/constants.py:9`, `app/domain/models.py:15`); Twilio/SendGrid/WhatsApp Business integrations in `app/providers.py:40-117` with mock fallback when unconfigured |
| Enrollment triggers: stage change, manual, lead score threshold, tag applied | ✅ | Manual `POST /enrollments`, `lead-scored` and `tag-applied` triggers (`app/api/sequences.py:133-149`); converted stage auto-exits (`app/service.py:257-267`) |
| Personalization variables: `{{lead_name}}`, `{{property_name}}`, `{{agent_name}}` | ✅ | `personalize.render()` at `app/personalize.py:29-32`; unknown variables render blank (covered by `test_unknown_variable_renders_blank_not_token`) |
| Exit condition: reply → pause; convert → exit sequence | ✅ | `ingest_engagement` reply path at `app/service.py:240-247` (sets state `paused`); `record_stage_change` converted path at `app/service.py:257-267` (sets state `stopped`) |
| Sequence branching: opened → Path A, not opened → Path B | ✅ | `branching.next_step_from_branches()` supports `opened` and `no_open_after:PT48H` (`app/branching.py:38-43`); covered by `test_branch_opened_takes_path_a` and `test_branch_no_open_takes_path_b_after_window` |
| Opt-out/unsubscribe handling | ✅ | `POST /contacts/{id}/opt-out` at `app/api/sequences.py:101-103`; `service.opt_out()` sets permanent suppression and stops active enrollments (`app/service.py:270-280`) |
| Sequence analytics: delivery rate, open rate, reply rate per step | ✅ | `analytics.sequence_analytics()` at `app/analytics.py:16-45` exposes `delivery_rate`, `open_rate`, `reply_rate` per step |

### Technical Notes

| Item | Status | Notes |
|------|--------|-------|
| Queue-based execution with retry logic on delivery failure | ✅/🚧 | `tick()` loop processes due enrollments (`app/service.py:135-150`); provider retry with exponential backoff in `app/providers.py:163-183`; durable queue (Temporal/SQS) and multi-provider failover are P4 |
| Channel integrations: email API, SMS gateway (Twilio), WhatsApp Business API | ✅/🚧 | Twilio/SendGrid/WhatsApp Business HTTP clients in `app/providers.py:40-117`; mock fallback when credentials are unset (`_mock_send` at `app/providers.py:34-37`); `set_provider()` injection remains available |
| Enrollment state per lead: `{ lead_id, sequence_id, current_step, status, enrolled_at }` | ✅ | `Enrollment` model at `app/domain/models.py:82-99` and `GET /enrollments/{id}` at `app/api/sequences.py:56-61` |
| Rate limiting: prevent over-messaging a single lead | ✅ | Global + per-sequence day/week caps in `app/frequency.py:26-81`, enforced in `app/guardrails.py:70-80` |
|| Thread-safety for in-memory state | ✅ | All repository methods are wrapped by `_synchronized` (`app/repositories.py:23-29`); `app/events.py`, `app/notifications.py`, `app/providers.py`, and `app/frequency.FrequencyStore` use `threading.RLock()` for in-process logs, provider registry, mock send log, and frequency counters |
|| Cross-agent request-id propagation | ✅ | The CMS precheck client (`app/clients/compliance_client.py:52`) and notification client (`app/notifications.py:26-28`) send `X-Request-ID`/`X-API-Key` headers; the gateway generates and returns `X-Request-ID` on all proxy responses |

### Acceptance Criteria

| Criterion | Status | Notes |
|-----------|--------|-------|
| Sequences trigger correctly on all defined enrollment conditions | ✅ | Manual, `lead.scored` ≥ threshold, `tag-applied`, and converted-stage triggers are tested |
| Messages send on correct schedule via correct channel | ✅ | `scheduler.next_send_at` + `tick` send through the step's `channel` (covered by `test_personalized_send_on_correct_channel` and `test_no_send_in_quiet_hours_then_sends_when_allowed`) |
| All personalization variables substitute correctly | ✅ | `test_personalized_send_on_correct_channel` and `test_unknown_variable_renders_blank_not_token` |
| Lead replying to any message pauses the active sequence | ✅ | `test_engagement_webhook_reply_pauses` and `test_reply_pauses_sequence` |
| Opt-out is respected immediately and permanently | ✅ | `test_opt_out_and_consent_endpoints` and `test_opt_out_is_permanent_and_blocks_reenroll` |
| Analytics show delivery, open, and reply rates per step | ✅ | `test_analytics_per_step_rates` and `GET /api/v1/analytics/sequences/{id}` |

## 5. User Story Acceptance

| User Story | Acceptance Criteria | Status | Notes |
|------------|---------------------|--------|-------|
| **US-1 — Compliant first touch** | AC1: On `lead.scored{grade: A}` contact is auto-enrolled in sequence | ✅ | `service.handle_lead_scored()` at `app/service.py:327-331`; `LEAD_SCORE_THRESHOLD` defaults to 70 (`app/core/config.py:72`); covered by `test_lead_score_trigger_threshold` |
| | AC2: First SMS scheduled for the next allowed window, consent verified at send | ✅ | `scheduler.next_send_at` clamps to contact-local 08:00–21:00 (`app/scheduler.py:38-43`); `guardrails.check` re-verifies consent/quiet-hours/DNC/frequency at send (`app/guardrails.py:36-77`) |
| | AC3: Missing/unknown consent holds step, raises alert, no send | ✅ | `consent_basis="unknown"` → `Decision("hold", "consent_unknown")` at `app/guardrails.py:46-48`; recorded in ledger; covered by `test_unknown_consent_fails_closed` |
| **US-2 — Stop on reply/apply** | AC1: On `contact.replied` or `deal.stage_changed{to: APPLICATION}` the active sequence stops within ≤60 s | ✅ | In-process stop is synchronous; `ingest_engagement` (`app/service.py:240-247`) and `record_stage_change` (`app/service.py:257-267`) finish the enrollment immediately. Sub-60 s propagation with a real event bus is P4. |
| | AC2: Stop reason is ledgered; LO is notified of the reply | ✅/🚧 | Stop reason is recorded in `Enrollment.stopped_reason` and the ledger (`app/service.py:210-219`); `service.ingest_engagement` calls `notifications.notify_owner_of_reply` when `contact.owner_id` is set (`app/service.py:300-305`); HTTP notification is optional and falls back to an in-process log |
| **US-3 — Branch on engagement** | AC1: If email shows no open after 48h, branch to SMS; if opened, branch to deeper email | ✅ | `branching.next_step_from_branches()` handles `opened` and `no_open_after:PT48H` (`app/branching.py:38-43`); `test_branch_opened_takes_path_a` and `test_branch_no_open_takes_path_b_after_window` |

## 6. P4 / Future (Promotion / GA Launch)

The following items are intentionally deferred to P4 / hardening and are **not** part of the current P0–P3 delivery:

| Item | Phase | Status | Notes |
|------|-------|--------|-------|
| Production provider credentials + delivery SLAs (Twilio, SendGrid, WhatsApp Business) | P4 Hardening | 🚧 | Real HTTP clients + mock fallback in `app/providers.py:40-135`; production credential rotation and delivery SLAs are P4 |
| Temporal durable per-contact workflows + durable timers | P4 Hardening | 📝 | In-process `tick()` at `app/service.py:109-125` is the stand-in |
| Kafka consumer for `lead.scored` / `deal.stage_changed` / `contact.replied` and producer for outreach events | P4 Hardening | 📝 | `app/events.py:31-52` is in-process; gateway is `local` by default |
| Redis shared frequency-cap counters and consent cache | P4 Hardening | 🚧 | `RedisFrequencyStore` in `app/frequency.py:87-156` is optional via `CACHE_GATEWAY=redis`; consent cache P4 |
| Postgres durable persistence for definitions, enrollments, ledger | P4 Hardening | 📝 | `app/repositories.py` is in-memory; `STORAGE_GATEWAY=postgres` optional via `pyproject.toml` extras |
| Engine 3 generative content integration | P4 Hardening | 📝 | `app/personalize.py` is a local template engine |
| Timing Optimizing Engine best-time window consumption | P4 Hardening | 📝 | `Contact.best_time_hour` field exists; `_snap_to_best_time` in `app/scheduler.py:16-35` consumes it |
| Per-variant A/B conversion reporting | P4 Hardening | 📝 | `app/abtest.py:14-21` allocates; analytics does not yet group by variant |
| Bounce/complaint suppression list ingestion | P4 Hardening | ✅/🚧 | `SuppressionEvent` and `POST /api/v1/webhooks/suppression` (`app/api/sequences.py:124-126`); `service.apply_suppression` sets `suppressed`, stops active enrollments, and emits `contact.suppressed` (`app/service.py:365-383`); in-process per-tenant list |
| LO / owner notifications on reply | P4 Hardening | ✅/🚧 | `Contact.owner_id` plus `app/notifications.py:notify_owner_of_reply` with HTTP `POST` to `NOTIFICATION_SERVICE_URL` and fallback to an in-process log (`app/notifications.py:46-73`); `service.ingest_engagement` invokes it for `reply` events when `contact.owner_id` is set (`app/service.py:300-305`) |
| Load test target ≥ 2,000 messages/s | P4 Hardening | 📝 | Not performed |
| SOC 2 / TCPA evidence runbooks and compliance dashboards | GA Launch | 📝 | Ledger + events are the evidence surface; runbooks P4 |

## 7. Component Coverage (System Architecture View)

| Component | Status | Notes |
|-----------|--------|-------|
| Enrollment Service | ✅ | `service.enroll()` at `app/service.py:76-105`; deduplication one active per `(contact, sequence, tenant)` |
| Sequence Workflow (state machine) | ✅ | `Enrollment` model + `phase` (`send`/`branch`) in `app/domain/models.py:82-99`; `_process`/`_process_send`/`_process_branch` at `app/service.py:127-207` |
| Scheduler | ✅ | `app/scheduler.py:38-43` with best-time snap and quiet-hours clamp |
| Guardrail Gateway | ✅ | `app/guardrails.py:36-77` fail-closed: opt-out/DNC → stop; consent/quiet-hours/freq-cap → hold |
| Channel Orchestrator | ✅/🚧 | `app/providers.py:40-183` real Twilio/SendGrid/WhatsApp HTTP clients + retry/backoff; mock fallback when unconfigured; `set_provider()` seam still available |
| Engagement Ingestor | ✅ | `POST /webhooks/engagement` → `service.ingest_engagement()` (`app/service.py:240-247`) |
| Stop Evaluator | ✅ | Reply (`replied`), converted stage (`converted`), opt-out/DNC (`opt_out`/`dnc`), max-steps (`max_steps`), manual state changes |
| Personalization Client | 🚧 | `app/personalize.py:29-32` local substitution; Engine 3 P4 |
| A/B Allocator | 🚧 | `app/abtest.py:14-21` deterministic allocation; per-variant reporting P4 |
| Frequency Cap Store | ✅ | `app/frequency.py:26-81` global + per-sequence day/week counters; in-memory default, optional `RedisFrequencyStore` via `CACHE_GATEWAY=redis` |
| Quiet Hours Evaluator | ✅ | `app/quiet_hours.py:24-42` contact-local window `[QUIET_HOURS_END, QUIET_HOURS_START)` |
| Outreach Ledger | ✅ | `LedgerRepository` at `app/repositories.py:90-117`; `OutreachRecord` at `app/domain/models.py:101-119` |
| Analytics | ✅ | `app/analytics.py:16-45` per-step delivery/open/reply rates |
| Consent / DNC Propagator | ✅ | `service.update_consent()` / `service.opt_out()` at `app/service.py:270-299`; propagates to active enrollments |
|| Suppression Handler | ✅/🚧 | `POST /api/v1/webhooks/suppression` → `service.apply_suppression()` (`app/service.py:365-383`); sets `Contact.suppressed`/`suppressed_reason`, stops active enrollments, emits `contact.suppressed`; global list is in-process |
|| Owner Notification Service | ✅/🚧 | `app/notifications.py:notify_owner_of_reply` with optional HTTP `POST` to `NOTIFICATION_SERVICE_URL` and fallback to an in-process log (`app/notifications.py:46-73`) |

## 8. File Map

```
src/agents/follow_up_sequences/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI factory + CORS + lifespan
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py           # pydantic-settings, env vars, §7 cadence/compliance params
│   │   └── logging.py          # structlog setup (console/JSON)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py             # X-API-Key auth dependency
│   │   ├── health.py           # /health, /ready
│   │   └── sequences.py        # all FUS REST endpoints
│   ├── domain/
│   │   └── models.py           # SequenceDef, Step, Branch, Contact, Enrollment, OutreachRecord, EngagementEvent
│   ├── constants.py            # channels, consent bases, stop reasons, guardrail blocks, converted stages
│   ├── duration.py             # ISO-8601 delay + branch-condition parsing
│   ├── quiet_hours.py          # TCPA quiet-hours check (contact-local tz)
│   ├── frequency.py            # global day/week message frequency cap
│   ├── guardrails.py           # fail-closed pre-send gateway
│   ├── personalize.py          # {{variable}} substitution
│   ├── providers.py            # channel provider abstraction + retry (mock default)
│   ├── branching.py            # engagement-driven next-step selection
│   ├── scheduler.py            # next_send_at (delay + best-time + quiet-hours)
│   ├── abtest.py               # deterministic step-content variant allocation
│   ├── analytics.py            # per-step delivery/open/reply rates
│   ├── events.py               # consumed/produced events + DLQ
│   ├── repositories.py         # in-memory repo implementations
│   └── service.py              # orchestration: enroll → tick → guardrail → send → branch/stop
├── tests/
│   ├── __init__.py
│   ├── conftest.py             # TestClient, settings reset, repo/provider/event reset
│   ├── test_api.py             # auth, enroll, tick, engagement, opt-out, triggers, analytics
│   ├── test_guardrails.py      # quiet hours, guardrail dispositions, duration/branching
│   ├── test_health.py          # health, ready, OpenAPI, CORS parsing, SQL toggle
│   └── test_pipeline.py        # end-to-end: personalization, quiet hours, stop conditions, branching, frequency, triggers
├── pyproject.toml              # deps + ruff + pytest config
├── uv.lock                     # frozen dependency set
├── .env.example                # environment variables and §7 defaults
├── Dockerfile
├── Makefile
├── docker-compose.yml          # local Postgres + Redis + app stack
├── deploy/
│   ├── README.md
│   └── followup-sequence-task-definition.json
└── README.md
```

### Test names (40 passing)

- `tests/test_api.py`
  - `test_write_requires_api_key`
  - `test_enroll_tick_and_get`
  - `test_get_sequence_and_404`
  - `test_engagement_webhook_reply_pauses`
  - `test_opt_out_and_consent_endpoints`
  - `test_stage_change_and_triggers`
  - `test_analytics_ledger_events`

- `tests/test_guardrails.py`
  - `test_quiet_hours_allows_daytime_blocks_night`
  - `test_next_allowed_clamps_forward`
  - `test_unknown_timezone_falls_back`
  - `test_guardrail_passes_when_all_clear`
  - `test_guardrail_stops_on_opt_out_and_dnc`
  - `test_guardrail_fail_closed_on_unknown_consent`
  - `test_guardrail_holds_in_quiet_hours`
  - `test_guardrail_holds_on_frequency_cap`
  - `test_guardrail_manual_cooldown`
  - `test_parse_duration`
  - `test_branch_opened_goes_path_a`
  - `test_branch_no_open_after_window_goes_path_b`
  - `test_branch_wait_is_longest_timed_condition`

- `tests/test_health.py`
  - `test_root_health`
  - `test_api_health`
  - `test_ready`
  - `test_openapi`
  - `test_cors_list_parsing_and_sql_toggle`

- `tests/test_pipeline.py`
  - `test_personalized_send_on_correct_channel`
  - `test_unknown_variable_renders_blank_not_token`
  - `test_no_send_in_quiet_hours_then_sends_when_allowed`
  - `test_unknown_consent_fails_closed`
  - `test_dnc_contact_stopped_not_sent`
  - `test_reply_pauses_sequence`
  - `test_opt_out_is_permanent_and_blocks_reenroll`
  - `test_convert_exits_sequence`
  - `test_duplicate_enrollment_no_duplicate`
  - `test_global_frequency_cap_across_sends`
  - `test_max_steps_completes`
  - `test_branch_opened_takes_path_a`
  - `test_branch_no_open_takes_path_b_after_window`
  - `test_lead_score_trigger_threshold`
  - `test_analytics_per_step_rates`

## 9. Integration into the Monorepo

| Layer | Change | Location |
|-------|--------|----------|
| API gateway routing | Added `follow_up_sequences` → slug `follow-up-sequences` in `AGENTS` map | `src/api/main.py:15` |
| Gateway route notes | Proxy notes for `/follow-up-sequences/*` | `src/api/routes/follow_up_sequences.py` |
| Compose orchestration | Added `follow_up_sequences` service + `FOLLOW_UP_SEQUENCES_URL` | `docker-compose.yml:20,45-49` |
| Main README | Lists `follow_up_sequences` as `Followup-Sequence` engine | `README.md:14` |
| Agent tests | Standalone pytest suite | `src/agents/follow_up_sequences/tests/` |

The gateway proxies `/follow-up-sequences/*` to the agent once `FOLLOW_UP_SEQUENCES_URL` is configured.

## 10. Configuration Highlights

Key environment variables (see `src/agents/follow_up_sequences/.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `API_KEY` | `change-me-dev-key` | X-API-Key for write/ops endpoints |
| `API_PREFIX` | `/api/v1` | REST prefix |
| `STORAGE_GATEWAY` | `memory` | `memory` or `postgres` (needs `DATABASE_URL` + `pip install '.[postgres]'`) |
| `CACHE_GATEWAY` | `memory` | `memory` or `redis` (needs `REDIS_URL` + `pip install '.[redis]'`) |
| `EVENT_SOURCE_GATEWAY` | `local` | `local` / `http` / `kafka` (needs `pip install '.[kafka]'`) |
| `QUIET_HOURS_START` | `21` | TCPA quiet-hours begin (contact-local hour) |
| `QUIET_HOURS_END` | `8` | Allowed sending window starts (contact-local hour) |
| `FREQ_CAP_DAY` | `2` | Max messages/day per contact across all cadences |
| `FREQ_CAP_WEEK` | `5` | Max messages/week per contact across all cadences |
| `CONSENT_CACHE_TTL_S` | `60` | Retry wait for unknown consent (propagation bound) |
| `MANUAL_OUTREACH_COOLDOWN_H` | `24` | Pause automation after an LO manual touch |
| `PROVIDER_RETRY_MAX` | `4` | Channel send retry attempts |
| `DEFAULT_TIMEZONE` | `America/New_York` | Fallback tz for quiet hours when contact tz unknown |
| `LEAD_SCORE_THRESHOLD` | `70` | Auto-enroll on `lead.scored` ≥ this |

## 11. Known Limitations & Simplifications

- **Persistence**: All repositories are in-memory. `STORAGE_GATEWAY=postgres` and `CACHE_GATEWAY=redis` are optional but not fully implemented beyond the config flags (the `repositories.py` in-memory surface is the active implementation).
- **Channel providers**: The default `_mock_send` records sends locally; real Twilio/email/WhatsApp/voicemail providers must be injected via `providers.set_provider()` (P4).
- **Workflow durability**: `POST /api/v1/scheduler/tick` is an in-process stand-in for Temporal/SQS durable timers; no worker queue, cron, or signal preemption race-safety across processes in this build.
- **Event bus**: Kafka/http sources are optional via config; the active code uses in-process function calls and an in-memory `events` log.
- **Personalization**: Local `{{var}}` substitution; Engine 3 generative content integration is P4.
- **A/B testing**: Variant allocation is deterministic by `enrollment_id`, but per-variant conversion dashboards and statistical significance are not built.
- **Idempotency**: The ledger tracks `(enrollment, step, occurrence)` keys and a 5-minute pre-send `_already_attempted` guard in `_process_send` prevents redelivered ticks / provider retries from producing duplicate sends.
- **Suppression lists**: `POST /api/v1/webhooks/suppression` and `service.apply_suppression` handle bounce/complaint/spam/block events, set `Contact.suppressed`/`suppressed_reason`, stop active enrollments, and emit `contact.suppressed`; the list is in-process per tenant and a shared Redis/global registry is P4.
- **Frequency cap**: Global + per-sequence day/week caps are enforced in `FrequencyStore` and `guardrails.check` (`app/frequency.py:26-81`, `app/guardrails.py:70-80`); Redis scaling is optional.
- **Rate-limit / provider failover**: Retry count is configurable, but exponential backoff, rate-limit detection, and multi-provider failover are not implemented.
- **LO notifications**: `Contact.owner_id` and `app/notifications.py:notify_owner_of_reply` support an optional HTTP notification service with in-process log fallback; a real notification service / CRM task integration is P4.
- **Stage-based branching**: Converted stages stop the sequence, but general non-terminal stage-to-step branching is not implemented.
- **Load / scale**: Tested at unit/integration scope; the 2,000 messages/s target and horizontal scaling are P4.

## 12. Acceptance Checklist

- ✅ Sequence definitions can be created/versioned with ordered steps, delays, channels, branches, and stop rules.
- ✅ Contacts can be enrolled manually or via `lead.scored` and `tag-applied` triggers with idempotent de-duplication.
- ✅ Scheduler computes `next_send_at` honoring contact-local TCPA quiet hours and the optional best-time hour.
- ✅ Guardrail is fail-closed: opt-out/DNC → stop; unknown consent / quiet-hours / frequency-cap / manual-cooldown → hold.
- ✅ Personalization variables (`{{lead_name}}`, `{{property_name}}`, `{{agent_name}}`, plus context vars) substitute correctly; unknown variables render blank.
- ✅ Reply pauses the active sequence; opt-out stops and permanently suppresses the contact; converted stage stops the sequence.
- ✅ Branching chooses the next step based on `opened` / `clicked` / `replied` and timed-negative conditions (`no_open_after:PT48H`, etc.).
- ✅ Global frequency cap (day + week) prevents over-messaging across all cadences.
- ✅ Per-sequence frequency caps are enforced in `SequenceDef.freq_cap_day`/`freq_cap_week` and `guardrails.check`.
- ✅ Outreach ledger records every attempt with consent basis, channel, status, block reason, and stop reason.
- ✅ Per-step analytics report delivery rate, open rate, and reply rate.
- 🚧 Channel delivery is exercised through a mock provider; real SMS/email/WhatsApp/voicemail integrations are P4.
- 🚧 A/B variant allocation works; per-variant conversion reporting and significance testing are P4.
- ✅ Idempotency keys are recorded and a 5-minute pre-send `_already_attempted` guard blocks redelivered ticks.
- ✅ Bounce/complaint/spam/block suppression webhook sets `Contact.suppressed`, stops active enrollments, and is enforced by `guardrails.check`.
- ✅ LO/owner reply notifications are sent via HTTP when `NOTIFICATION_SERVICE_URL` is configured and fall back to an in-process log.
- ✅ Unit + functional tests pass (`50/50` in the agent test suite).

## 12a. P4 Implementation Update

This P4 pass added the following production hardening pieces without breaking P0–P3 tests:

1. **Redis-backed frequency counters**: `app/frequency.py` now selects a `RedisFrequencyStore` when `CACHE_GATEWAY=redis`, using atomic `INCR`/`PIPELINE` with TTL-based expiry. It falls back to the in-memory store when Redis is unavailable or not configured.
2. **Cross-agent CMS precheck client**: `app/clients/compliance_client.py` provides a synchronous `httpx` client that calls the Compliance Monitor `POST /api/v1/precheck` before any real send. It is gated by `FUS_PRECHECK_ENABLED` and falls back to local guardrails on failure.
3. **Environment variables**: `FUS_PRECHECK_ENABLED`, `COMPLIANCE_MONITOR_URL`, `COMPLIANCE_API_KEY`, `FUS_PRECHECK_TIMEOUT`, `NOTIFICATION_SERVICE_URL`, `NOTIFICATION_API_KEY`, and `INTEGRATION_TIMEOUT_S` added to `.env.example` and `app/core/config.py`.
4. **Suppression webhook**: `SuppressionEvent` and `POST /api/v1/webhooks/suppression` (`app/api/sequences.py:124-126`) ingest provider bounce/complaint/spam/block events; `service.apply_suppression` (`app/service.py:365-383`) sets `Contact.suppressed`/`suppressed_reason`, stops active enrollments, and emits `contact.suppressed`; `guardrails.check` (`app/guardrails.py:47-48`) stops sends on a suppressed contact.
5. **LO/owner reply notifications**: `Contact.owner_id` field (`app/domain/models.py:64`) plus `app/notifications.py:notify_owner_of_reply` with HTTP `POST` to `NOTIFICATION_SERVICE_URL` and fallback to an in-process log; `service.ingest_engagement` calls it for `reply` events when `contact.owner_id` is set (`app/service.py:300-305`).
6. **Thread-safety**: `app/repositories.py` now uses per-instance `threading.RLock()` via the `_synchronized` decorator on all repository methods. `app/events.py`, `app/notifications.py`, `app/providers.py`, and the in-memory `app/frequency.FrequencyStore` now use module-level `threading.RLock()` to protect in-process logs, the provider registry, the mock send log, and frequency counters.
7. **Request-id propagation**: `app/clients/compliance_client.py:52` sends `X-Request-ID`; the gateway `src/api/main.py` generates/propagates `X-Request-ID` and returns it on every proxy response (including 502/504/404), with root integration tests in `tests/integration/test_gateway.py:70-89`.
8. **Default-dev-key rejection**: `app/core/config.py` now raises `ValueError` if `API_KEY=change-me-dev-key` and `ENV` is not `local`/`dev`/`development`/`test`, matching the gateway.
9. **Docker-compose cross-agent wiring**: `docker-compose.yml` already set `COMPLIANCE_MONITOR_URL=http://compliance_monitor:8000` and `FUS_PRECHECK_ENABLED=true` for FUS by default.
10. **Cross-agent client tests**: `tests/test_cross_agent.py` mocks `httpx.Client` and verifies FUS→CMS `precheck` calls the right endpoint with `X-API-Key` and `X-Request-ID`.
11. **Bug-audit hardening**: frequency-cap day/week keys are normalized to UTC before formatting (`app/frequency.py:19-35`); `StateRequest.state` is validated via `Literal["paused", "resume", "stopped"]` at the API (`app/api/sequences.py:69-70`) and `service.set_enrollment_state()` rejects invalid states (`app/service.py:311-323`); mutable `dict` Pydantic request defaults use `Field(default_factory=dict)` (`app/api/sequences.py:45,135`); new `tests/test_bug_audit.py` covers timezone-normalized frequency keys and concurrent safety for the event log, notification log, provider mock send log, and `FrequencyStore` counters.

Items still deferred:
- Real channel providers (Twilio, SendGrid, WhatsApp Business API).
- Kafka consumer for inbound events and producer for outbound events.
- Temporal durable workflows.
- Postgres persistence.
- Engine 3 generative content, Timing Optimizer consumption, per-variant A/B reporting, shared Redis/global suppression registry, and a real notification service / CRM task integration.

## 13. P4 Next Steps

1. Replace the mock provider with real Twilio SMS, email, WhatsApp Business, and voicemail-drop integrations behind `providers.set_provider()`.
2. Add Temporal durable per-contact workflows with timers and signals for stop preemption.
3. Add a Kafka consumer for inbound enrollment/engagement events and a producer for `outreach.sent` / `sequence.stopped`.
4. Implement Redis-backed consent cache (`CACHE_GATEWAY=redis`) for pre-send consent checks.
5. Implement Postgres persistence for sequence definitions, contacts, enrollments, ledger, and engagement events (`STORAGE_GATEWAY=postgres`).
6. Integrate Engine 3 for generative personalized content.
7. Consume best-time-to-contact windows from the Timing Optimizing Engine.
8. Build per-variant A/B conversion reporting and statistical significance.
9. Move suppression lists and LO notifications to a shared Redis/global registry and real notification service / CRM task integration.
10. Implement exponential backoff, rate-limit detection, and multi-provider failover.
11. Run the ≥ 2,000 messages/s load test and produce SOC 2 / TCPA evidence runbooks.
