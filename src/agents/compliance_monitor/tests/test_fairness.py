"""Fair-lending disparity monitor (FR-005 / US-2): the 4/5ths rule + minimum-sample guard."""
from app import service
from app.core.config import get_settings
from app.fairness import disparity
from app.repositories import get_alert_repository, get_case_repository


def test_four_fifths_ratio_and_pass_fail():
    settings = get_settings()
    cohorts = {"a": {"selected": 42, "total": 100}, "b": {"selected": 31, "total": 100}}
    r = disparity("lead_assignment", "cohort", cohorts, settings=settings)
    assert r.selection_rates == {"a": 0.42, "b": 0.31}
    assert round(r.ratio, 2) == 0.74           # 0.31 / 0.42
    assert r.passes is False and r.min_sample_met is True


def test_passing_when_within_band():
    settings = get_settings()
    cohorts = {"a": {"selected": 40, "total": 100}, "b": {"selected": 36, "total": 100}}
    r = disparity("lead_assignment", "cohort", cohorts, settings=settings)
    assert r.ratio >= settings.fairness_ratio_min and r.passes is True


def test_disparity_opens_a_critical_case():
    cohorts = {"a": {"selected": 42, "total": 100}, "b": {"selected": 31, "total": 100}}
    service.evaluate_fairness("lead_assignment", "cohort", cohorts)
    assert any(a.domain == "ai_fairness" and a.severity == "critical"
               for a in get_alert_repository().list())
    # Critical alert auto-opens a case.
    assert any(c.severity == "critical" for c in get_case_repository().list())


def test_small_sample_does_not_alert():
    # Same disparate ratio but below the 100-sample floor → monitored, not alerted.
    cohorts = {"a": {"selected": 4, "total": 10}, "b": {"selected": 3, "total": 10}}
    r = service.evaluate_fairness("lead_assignment", "cohort", cohorts)
    assert r.passes is False and r.min_sample_met is False
    assert get_alert_repository().list() == []
