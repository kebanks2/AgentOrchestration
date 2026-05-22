"""Canary rollout analysis for worker-backed deployments."""

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class CanaryThresholds:
    max_error_rate: float = 0.05
    max_queue_backlog: int = 100
    max_processing_latency_ms: float = 1000.0
    max_lease_renewal_failures: int = 0


@dataclass(frozen=True)
class CanaryMetrics:
    http_healthy: bool
    error_rate: float
    queue_backlog: int
    processing_latency_ms: float
    lease_renewal_failures: int
    scheduler_depth: int = 0
    active_workers: int = 0


@dataclass(frozen=True)
class CanaryDecision:
    promote: bool
    rollback: bool
    reasons: List[str]
    dashboard: Dict[str, Dict[str, float]]


class CanaryAnalyzer:
    def __init__(self, thresholds: CanaryThresholds = CanaryThresholds()):
        self.thresholds = thresholds

    def analyze(self, metrics: CanaryMetrics) -> CanaryDecision:
        _validate_metrics(metrics)
        reasons = []

        if not metrics.http_healthy:
            reasons.append("http_unhealthy")
        if metrics.error_rate > self.thresholds.max_error_rate:
            reasons.append("error_rate_exceeded")
        if metrics.queue_backlog > self.thresholds.max_queue_backlog:
            reasons.append("queue_backlog_exceeded")
        if (
            metrics.processing_latency_ms
            > self.thresholds.max_processing_latency_ms
        ):
            reasons.append("processing_latency_exceeded")
        if (
            metrics.lease_renewal_failures
            > self.thresholds.max_lease_renewal_failures
        ):
            reasons.append("lease_renewal_failures")

        return CanaryDecision(
            promote=not reasons,
            rollback=bool(reasons),
            reasons=reasons,
            dashboard=_dashboard(metrics),
        )


def analyze_worker_canary(
    metrics: CanaryMetrics,
    thresholds: CanaryThresholds = CanaryThresholds(),
) -> CanaryDecision:
    return CanaryAnalyzer(thresholds).analyze(metrics)


def _dashboard(metrics: CanaryMetrics) -> Dict[str, Dict[str, float]]:
    return {
        "scheduler": {
            "queue_backlog": metrics.queue_backlog,
            "scheduler_depth": metrics.scheduler_depth,
        },
        "worker": {
            "processing_latency_ms": metrics.processing_latency_ms,
            "lease_renewal_failures": metrics.lease_renewal_failures,
            "active_workers": metrics.active_workers,
        },
        "http": {
            "healthy": float(metrics.http_healthy),
            "error_rate": metrics.error_rate,
        },
    }


def _validate_metrics(metrics: CanaryMetrics) -> None:
    values = {
        "error_rate": metrics.error_rate,
        "queue_backlog": metrics.queue_backlog,
        "processing_latency_ms": metrics.processing_latency_ms,
        "lease_renewal_failures": metrics.lease_renewal_failures,
        "scheduler_depth": metrics.scheduler_depth,
        "active_workers": metrics.active_workers,
    }
    invalid = [name for name, value in values.items() if value < 0]
    if invalid:
        raise ValueError(
            "canary metrics must be non-negative: " + ", ".join(invalid)
        )
