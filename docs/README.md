# CRM Operations Platform Service — Documentation

This directory contains the product, architecture, implementation, and testing documentation for the five consolidated sub-agents of the CRM AI Operations Platform Service.

## Ticket Descriptions (Source of Truth)

| Agent | Ticket |
|-------|--------|
| Compliance Monitor System | [ticket_description_cms.md](ticket_description_cms.md) |
| Feedback Loop System | [ticket_description_fls.md](ticket_description_fls.md) |
| Follow-up Sequences Engine | [ticket_description_fus.md](ticket_description_fus.md) |
| Performance Analytics System | [ticket_description_pas.md](ticket_description_pas.md) |
| Task Automation Engine | [ticket_description_tas.md](ticket_description_tas.md) |

## Implementation Status

| Agent | Implementation Status |
|-------|----------------------|
| Compliance Monitor System | [implementation_status_cms.md](implementation_status_cms.md) |
| Feedback Loop System | [implementation_status_fls.md](implementation_status_fls.md) |
| Follow-up Sequences Engine | [implementation_status_fus.md](implementation_status_fus.md) |
| Performance Analytics System | [implementation_status_pas.md](implementation_status_pas.md) |
| Task Automation Engine | [implementation_status_tas.md](implementation_status_tas.md) |

## P4 Promotion / GA Launch

- [P4_BLOCKERS.md](P4_BLOCKERS.md) — remaining blockers that must be resolved before production hardening and GA launch.

## Testing

- [TESTING.md](TESTING.md) — how to run each agent's test suite, lint/format commands, and CI notes.
- [ENGINE_TESTING_RESULTS.md](ENGINE_TESTING_RESULTS.md) — latest per-engine test results and smoke-test outcomes.
- [RemainingWork.md](RemainingWork.md) — remaining implementation and configuration checklist before production.
- [DEPLOYMENT_READINESS.md](DEPLOYMENT_READINESS.md) — deployment readiness assessment and production blockers.

## Architecture & Operations

- [AGENT_ORCHESTRATION.md](agent_communication.md) — how the five agents communicate (gateway, events, shared services, integration points).
- [EXTERNAL_API_KEYS.md](external_api_keys.md) — required keys and secrets for local and production use.
- [DEPLOYMENT_READINESS.md](DEPLOYMENT_READINESS.md) — ticket coverage, readiness checklist, and production blockers.
- [BUG_AUDIT_REPORT.md](bug_audit_report.md) — bug/edge-case audit, fixes applied, and remaining open items.

## Repository Structure

- [crm_ai_ops_codebase_structure.md](crm_ai_ops_codebase_structure.md) — notes on the monorepo layout and where each engine lives.

## New Agent Report

- [new_agents_final_report.md](new_agents_final_report.md) — summary of the CMS, FUS, and TAS initial delivery and test results.
