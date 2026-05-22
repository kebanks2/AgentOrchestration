import pytest

from src.orchestrator.canary import (
    CanaryAnalyzer,
    CanaryMetrics,
    CanaryThresholds,
    analyze_worker_canary,
)


def _healthy_metrics(**overrides):
    values = {
        "http_healthy": True,
        "error_rate": 0.01,
        "queue_backlog": 8,
        "processing_latency_ms": 120.0,
        "lease_renewal_failures": 0,
        "scheduler_depth": 10,
        "active_workers": 4,
    }
    values.update(overrides)
    return CanaryMetrics(**values)


def test_canary_promotes_when_worker_metrics_are_within_thresholds():
    decision = analyze_worker_canary(_healthy_metrics())

    assert decision.promote is True
    assert decision.rollback is False
    assert decision.reasons == []


def test_canary_fails_promotion_when_queue_backlog_exceeds_threshold():
    analyzer = CanaryAnalyzer(CanaryThresholds(max_queue_backlog=25))

    decision = analyzer.analyze(_healthy_metrics(queue_backlog=26))

    assert decision.promote is False
    assert decision.rollback is True
    assert decision.reasons == ["queue_backlog_exceeded"]


def test_canary_fails_promotion_when_processing_latency_exceeds_threshold():
    analyzer = CanaryAnalyzer(
        CanaryThresholds(max_processing_latency_ms=250.0)
    )

    decision = analyzer.analyze(
        _healthy_metrics(processing_latency_ms=251.0)
    )

    assert decision.promote is False
    assert "processing_latency_exceeded" in decision.reasons


def test_canary_lease_failures_contribute_to_rollback():
    analyzer = CanaryAnalyzer(
        CanaryThresholds(max_lease_renewal_failures=1)
    )

    decision = analyzer.analyze(_healthy_metrics(lease_renewal_failures=2))

    assert decision.promote is False
    assert decision.rollback is True
    assert "lease_renewal_failures" in decision.reasons


def test_canary_dashboard_groups_scheduler_worker_and_http_metrics():
    decision = analyze_worker_canary(_healthy_metrics())

    assert decision.dashboard["scheduler"] == {
        "queue_backlog": 8,
        "scheduler_depth": 10,
    }
    assert decision.dashboard["worker"] == {
        "processing_latency_ms": 120.0,
        "lease_renewal_failures": 0,
        "active_workers": 4,
    }
    assert decision.dashboard["http"] == {
        "healthy": 1.0,
        "error_rate": 0.01,
    }


def test_canary_reports_all_worker_rollback_reasons_together():
    analyzer = CanaryAnalyzer(
        CanaryThresholds(
            max_queue_backlog=25,
            max_processing_latency_ms=250.0,
            max_lease_renewal_failures=0,
        )
    )

    decision = analyzer.analyze(
        _healthy_metrics(
            queue_backlog=26,
            processing_latency_ms=251.0,
            lease_renewal_failures=1,
        )
    )

    assert decision.promote is False
    assert decision.rollback is True
    assert decision.reasons == [
        "queue_backlog_exceeded",
        "processing_latency_exceeded",
        "lease_renewal_failures",
    ]


def test_canary_rejects_negative_worker_metrics():
    with pytest.raises(ValueError, match="queue_backlog"):
        analyze_worker_canary(_healthy_metrics(queue_backlog=-1))
