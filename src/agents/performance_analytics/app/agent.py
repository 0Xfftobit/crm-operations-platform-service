"""Performance Analytics Agent — public orchestration facade."""

from __future__ import annotations

from app import service


class PerformanceAnalyticsAgent:
    """Engine-level agent for the Performance Analytics System."""

    ingest_touch = staticmethod(service.ingest_touch)
    ingest_outcome = staticmethod(service.ingest_outcome)
    ingest_campaign_cost = staticmethod(service.ingest_campaign_cost)
    ingest_finance_source = staticmethod(service.ingest_finance_source)
    publish_metric = staticmethod(service.publish_metric)
    list_metrics = staticmethod(service.list_metrics)
    get_metric = staticmethod(service.get_metric)
    get_kpi = staticmethod(service.get_kpi)
    build_funnel = staticmethod(service.build_funnel)
    build_rep_scorecards = staticmethod(service.build_rep_scorecards)
    build_rep_scorecard = staticmethod(service.build_rep_scorecard)
    build_leaderboard = staticmethod(service.build_leaderboard)
    build_channel_effectiveness = staticmethod(service.build_channel_effectiveness)
    build_channel_heatmaps = staticmethod(service.build_channel_heatmaps)
    build_channel_recommendations = staticmethod(service.build_channel_recommendations)
    build_cohorts = staticmethod(service.build_cohorts)
    compute_ab_uplift = staticmethod(service.compute_ab_uplift)
    compute_attribution = staticmethod(service.compute_attribution)
    compute_all_attribution = staticmethod(service.compute_all_attribution)
    attribution_summary = staticmethod(service.attribution_summary)
    reconcile = staticmethod(service.reconcile)
    reconcile_all = staticmethod(service.reconcile_all)
    get_reconciliations = staticmethod(service.get_reconciliations)
    detect_anomalies = staticmethod(service.detect_anomalies)
    threshold_alert = staticmethod(service.threshold_alert)
    get_alerts = staticmethod(service.get_alerts)
    schedule_report = staticmethod(service.schedule_report)
    list_reports = staticmethod(service.list_reports)
    run_report = staticmethod(service.run_report)
    export_metric = staticmethod(service.export_metric)
    get_stats = staticmethod(service.get_stats)
    reset_state = staticmethod(service.reset_state)
    seed = staticmethod(service.seed)
