import pytest

from src.deploy import (
    WorkerResourceValidationError,
    validate_worker_resource_requests,
)


def test_worker_manifest_accepts_direct_resource_requests():
    manifest = {
        "kind": "worker",
        "spec": {
            "workload_class": "standard",
            "resources": {
                "requests": {
                    "cpu": "750m",
                    "memory": "768Mi",
                },
            },
        },
    }

    summary = validate_worker_resource_requests(manifest)

    assert summary == {
        "workload_class": "standard",
        "cpu_millicores": 750,
        "memory_mib": 768,
        "min_cpu_millicores": 500,
        "min_memory_mib": 512,
    }


def test_worker_manifest_accepts_kubernetes_container_requests():
    manifest = {
        "kind": "Deployment",
        "metadata": {"labels": {"workload-class": "background"}},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "scheduler-worker",
                            "resources": {
                                "requests": {
                                    "cpu": "0.5",
                                    "memory": "1Gi",
                                },
                            },
                        },
                    ],
                },
            },
        },
    }

    summary = validate_worker_resource_requests(manifest)

    assert summary["workload_class"] == "background"
    assert summary["cpu_millicores"] == 500
    assert summary["memory_mib"] == 1024


def test_worker_manifest_rejects_missing_resource_requests():
    manifest = {
        "kind": "worker",
        "spec": {"workload_class": "background"},
    }

    with pytest.raises(WorkerResourceValidationError, match="required"):
        validate_worker_resource_requests(manifest)


def test_worker_manifest_rejects_requests_below_policy_minimums():
    manifest = {
        "kind": "worker",
        "spec": {
            "workload_class": "high-memory",
            "resources": {
                "requests": {
                    "cpu": "500m",
                    "memory": "1024Mi",
                },
            },
        },
    }

    with pytest.raises(WorkerResourceValidationError, match="below 1000m"):
        validate_worker_resource_requests(manifest)


def test_worker_manifest_rejects_unknown_workload_class():
    manifest = {
        "kind": "worker",
        "spec": {
            "workload_class": "gpu",
            "resources": {
                "requests": {
                    "cpu": "1",
                    "memory": "2Gi",
                },
            },
        },
    }

    with pytest.raises(WorkerResourceValidationError, match="Unknown"):
        validate_worker_resource_requests(manifest)


def test_non_worker_manifest_is_not_validated():
    manifest = {
        "kind": "api",
        "spec": {},
    }

    assert validate_worker_resource_requests(manifest) is None
