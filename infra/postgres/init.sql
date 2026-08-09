-- Per-agent databases for the CRM Operations Platform staging/prod stack.
-- Postgres will create the POSTGRES_DB (crm_ops) automatically; this script
-- creates the additional agent-specific databases used by the docker-compose.

CREATE DATABASE compliance_monitor;
CREATE DATABASE followup_sequence;
CREATE DATABASE performance_analytics;
CREATE DATABASE task_automation;
CREATE DATABASE feedback_loop;
