"""Gateway route notes for performance_analytics.

The real service lives in ``src/agents/performance_analytics/`` and exposes the
full Performance Analytics System REST API. The gateway proxies
``/performance-analytics/*`` to that service.

Implemented scope: P0–P3 of the PAS ticket plus ROI Attribution and Channel
Effectiveness subtasks. Includes governed KPIs, multi-touch attribution, funnel/
velocity, rep/team scorecards, channel effectiveness, cohorts, A/B uplift,
finance reconciliation, anomaly/threshold alerts, and CSV/PDF report export.
"""
