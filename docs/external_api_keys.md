# External API Key Requirements

This document lists external APIs and keys that may be needed when moving the five CRM AI agents from their current in-memory/stub implementations to production.

## Current State (P0–P3)

The current implementations are intentionally **self-contained and stubbed** for local development and testing. **No external API keys are required to run the unit tests or the local FastAPI services.** The few integrations that would normally call external providers are implemented as deterministic stubs or in-process logs.

| Engine | External Service | Required Today? | Notes |
|--------|------------------|-----------------|-------|
| Feedback Loop System | OpenAI / Gemini / Anthropic | No | Model training uses `scikit-learn` `LogisticRegression`. |
| Performance Analytics System | OpenAI / Gemini / Anthropic | No | All analytics, attribution, and PDF export are rule/statistical. |
| Compliance Monitor System | OpenAI / Gemini / Anthropic | No | PII detection is regex/pattern-based; no LLM. |
| Follow-up Sequences Engine | OpenAI / Gemini / Anthropic | No | Message content uses templates (`app/personalize.py`); no LLM generation. |
| Task Automation Engine | OpenAI / Gemini / Anthropic | **No for current stub** | `app/agents/llm_task_agent.py` is a deterministic stub with confidence/approval guardrails. A real LLM provider would need an API key in P4. |

## Optional / P4 External Services

When the platform is promoted to production, the following keys and endpoints will likely be needed depending on deployment choices:

### LLM Provider (Task Automation Engine)

- **Use case**: Drafting task notes, classifying free-text notes, summarizing threads under human approval.
- **Current implementation**: `app/agents/llm_task_agent.py` returns deterministic stubs.
- **Production swap**: Replace the stub functions with calls to OpenAI (`OPENAI_API_KEY`), Google Gemini (`GOOGLE_API_KEY`), or Anthropic Claude (`ANTHROPIC_API_KEY`).
- **Where to configure**: Add to `.env` and read in `app/core/config.py` (e.g., `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`).

### Channel Providers (Follow-up Sequences Engine)

- **Use case**: Sending real SMS, email, WhatsApp, voicemail drops.
- **Current implementation**: `app/providers.py` uses in-memory `MockProvider` and logs sends.
- **Production swap**: Twilio (`TWILIO_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`) for SMS/voice; SendGrid/Amazon SES (`SENDGRID_API_KEY`) for email; WhatsApp Business API credentials for WhatsApp.
- **Where to configure**: Add provider configs to `app/core/config.py` and `app/providers.py`.

### CRM Service API (Task Automation Engine)

- **Use case**: Creating tasks, updating records, requesting documents via the real CRM.
- **Current implementation**: `app/integrations/crm.py` and `app/integrations/documents.py` are stubs returning synthetic records.
- **Production swap**: HTTP API key or OAuth token for the internal CRM service (`CRM_BASE_URL`, `CRM_API_KEY`).

### Calendar APIs (Task Automation Engine)

- **Use case**: Scheduling meeting tasks.
- **Current implementation**: `app/integrations/calendar.py` stub.
- **Production swap**: Google Calendar / Microsoft Graph OAuth (`GOOGLE_CLIENT_*`, `MICROSOFT_CLIENT_*`).

### Kafka / Temporal Infrastructure

- **Use case**: Cross-agent event streaming and durable workflow orchestration.
- **Current implementation**: In-process buses and local Temporal stubs.
- **Production swap**: Kafka bootstrap servers (no key, but TLS certs/SASL may be needed); Temporal server (`TEMPORAL_HOST`, mTLS certificates).

### S3 / Object Lock (Compliance Monitor System)

- **Use case**: WORM audit mirror and cold storage.
- **Current implementation**: Local filesystem `WORM_GATEWAY=local`.
- **Production swap**: AWS credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) for S3 Object Lock / Glacier (`WORM_GATEWAY=s3`, `COLD_STORE_GATEWAY=s3`).

### OpenTelemetry / Observability

- **Use case**: Distributed tracing.
- **Current implementation**: Disabled by default (`TRACING_ENABLED=false`).
- **Production swap**: OTLP endpoint (e.g., `OTEL_EXPORTER_OTLP_ENDPOINT`), often no key for self-hosted, but API tokens for SaaS vendors (Honeycomb, Datadog, etc.).

## P4–P5 Additional Keys & Credentials

The section above covers the core P4 swap-ins. Once you move into the full P4 Integration / P5 Hardening / GA Launch scope described in the implementation status docs, the following additional external services and API keys/credentials are likely to be required.

### Consent / DNC / Regulatory Feeds

| Service | Agents | Use case | Typical keys/credentials |
|---------|--------|----------|--------------------------|
| TCPA / DNC registry or enterprise consent service | CMS, FUS | Real-time DNC and consent verification before sends | `CONSENT_API_KEY`, `CONSENT_API_URL`, `DNC_REGISTRY_API_KEY` |
| Federal Register / CFPB / HUD / FTC legal-update API | CMS | Automated regulation monitoring for `app/regwatch.py` | `REGWATCH_API_KEY`, `REGWATCH_FEED_URL` |
| Compliance notary / Merkle timestamp service | CMS | External tamper-evidence anchoring for WORM audit chain | `NOTARY_API_KEY`, `NOTARY_ENDPOINT`, signing certificate |

### Data Warehouse / Batch Analytics

| Service | Agents | Use case | Typical keys/credentials |
|---------|--------|----------|--------------------------|
| Snowflake / BigQuery / Redshift / Databricks | CMS, PAS, FLS | Batch fair-lending analytics, materialized KPIs, training dataset storage | `DW_ACCOUNT`, `DW_USER`, `DW_PASSWORD`, `DW_WAREHOUSE`, service-account key JSON |
| dbt Cloud or similar transformation scheduler | PAS, CMS | KPI materialization and reconciliation pipelines | `DBT_API_KEY`, `DBT_JOB_ID` |

### BI / Dashboards

| Service | Agents | Use case | Typical keys/credentials |
|---------|--------|----------|--------------------------|
| Looker / Tableau / Superset / Metabase | CMS, PAS | Rendered RAG dashboards, case queues, funnel charts, executive reporting | `BI_EMBED_SECRET`, `BI_API_KEY`, `BI_BASE_URL` |

### Identity / SSO / RBAC

| Service | Agents | Use case | Typical keys/credentials |
|---------|--------|----------|--------------------------|
| Auth0 / Okta / AWS Cognito / Azure AD | All 5 agents | OIDC/SSO login, role/branch/tenant policy enforcement | `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_ISSUER`, `JWKS_URI` |

### MLOps / Model Registry

| Service | Agent | Use case | Typical keys/credentials |
|---------|-------|----------|--------------------------|
| MLflow / Weights & Biases / Comet | FLS | Real model registry, artifact lineage, experiment tracking | `MLFLOW_TRACKING_URI`, `MLFLOW_API_KEY` or `WANDB_API_KEY` |
| Feature store (Feast / Tecton / custom) | FLS, TAS | Point-in-time feature reads for training and inference | `FEATURE_STORE_API_KEY`, `FEATURE_STORE_REGISTRY` |

### Alerting / Notifications

| Service | Agents | Use case | Typical keys/credentials |
|---------|--------|----------|--------------------------|
| Slack / PagerDuty / Opsgenie | FLS, PAS, CMS | Drift/reconciliation/SLA breach alerts and on-call paging | `SLACK_WEBHOOK_URL`, `PAGERDUTY_INTEGRATION_KEY`, `OPSGENIE_API_KEY` |
| SendGrid / Amazon SES / Mailgun | PAS, FUS, TAS | Scheduled report emails and LO/owner notifications | `SENDGRID_API_KEY`, `SES_ACCESS_KEY`, `MAILGUN_API_KEY` |

### Bounce / Complaint / Suppression Webhooks

| Service | Agent | Use case | Typical keys/credentials |
|---------|-------|----------|--------------------------|
| Twilio / SendGrid / Amazon SES event APIs | FUS | Ingest bounce, complaint, delivery-failure events for suppression list | `TWILIO_AUTH_TOKEN`, `SENDGRID_WEBHOOK_SECRET`, `SES_SNS_TOPIC` |

### Additional Engine Integrations

| Service | Agent | Use case | Typical keys/credentials |
|---------|-------|----------|--------------------------|
| Engine 3 generative-content / template service | FUS | AI-generated or dynamic message templates beyond `{{var}}` substitution | `ENGINE3_API_KEY`, `ENGINE3_BASE_URL` (or reuse `LLM_API_KEY`) |
| Timing Optimizing Engine | FUS | Best-time-to-send recommendations per contact | `TIMING_ENGINE_API_KEY`, `TIMING_ENGINE_URL` |
| Forecasting Engine | PAS | Forecast-attainment targets and predictive KPIs | `FORECAST_ENGINE_API_KEY`, `FORECAST_ENGINE_URL` |
| Internal CRM service | TAS | Record updates, task creation, document requests | `CRM_BASE_URL`, `CRM_API_KEY` or OAuth client credentials |
| Google / Microsoft calendar OAuth | TAS | Meeting scheduling | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `MICROSOFT_CLIENT_SECRET` |

### Observability / Secret Management

| Service | Agents | Use case | Typical keys/credentials |
|---------|--------|----------|--------------------------|
| Datadog / Honeycomb / New Relic / Grafana Cloud | All | SaaS APM / distributed tracing / metrics dashboards | `DD_API_KEY`, `HONEYCOMB_API_KEY`, `NEW_RELIC_LICENSE_KEY`, `GRAFANA_CLOUD_API_KEY` |
| AWS Secrets Manager / HashiCorp Vault / Doppler | All | Centralized secret storage and rotation | `VAULT_ADDR`, `VAULT_TOKEN`, IAM role for Secrets Manager |

### Infrastructure (managed/cloud credentials)

| Service | Agents | Use case | Typical keys/credentials |
|---------|--------|----------|--------------------------|
| AWS S3 + S3 Object Lock / Glacier | CMS, FLS, PAS | WORM mirror, cold storage, artifact/ dataset storage | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` or IRSA role |
| Kafka / Confluent / Redpanda | CMS, FUS, TAS, FLS | Cross-agent streaming; schema registry | `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_API_KEY`, `KAFKA_API_SECRET`, `SCHEMA_REGISTRY_API_KEY` |
| Temporal Cloud / self-hosted Temporal | TAS, FUS | Durable workflow orchestration | `TEMPORAL_HOST`, `TEMPORAL_NAMESPACE`, client mTLS cert/key |
| Redis Cloud / Upstash / AWS ElastiCache | All | Shared cache, frequency counters, idempotency | Redis URL with auth: `redis://:password@host` or `REDIS_PASSWORD` |
| Postgres (RDS / Cloud SQL / AlloyDB) | All | Durable persistence | `DATABASE_URL` or `DB_HOST`, `DB_USER`, `DB_PASSWORD` |

## Summary

For the current P0–P3 scope, **no OpenAI, Gemini, or paid external API keys are needed** to build, test, and run the agents locally. The only required "external" services are the Python/uv toolchain and the dependencies already declared in each `pyproject.toml`.

When moving to production (P4), plan for the core swap-ins first:

- **Task Automation**: one LLM provider API key if LLM-drafted notes are enabled.
- **Follow-up Sequences**: Twilio/SendGrid/WhatsApp credentials for real sends.
- **Task Automation + Compliance**: CRM service credentials and possibly calendar OAuth.
- **Infrastructure**: Kafka, Temporal, Postgres, Redis, S3 (IAM keys or IRSA/role-based access).

For the full P4–P5 hardening and GA launch, also budget for:

- **Consent/DNC/regulatory feeds** (CMS, FUS)
- **Data warehouse + dbt** (CMS, PAS, FLS)
- **BI dashboard tool** (CMS, PAS)
- **SSO/OIDC provider** (all agents)
- **MLflow + feature store** (FLS)
- **Alerting/notification services** (FLS, PAS, CMS)
- **Bounce/complaint webhooks** (FUS)
- **SaaS observability + secret management** (all agents)
- **Additional engine APIs** (Engine 3, Timing Engine, Forecasting Engine)
