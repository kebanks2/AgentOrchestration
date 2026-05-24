import pytest

from src.orchestrator.webhooks import DeliveryAuditStore, DeliveryRejected


def test_records_valid_delivery_and_removes_internal_fields():
    store = DeliveryAuditStore()
    store.register_endpoint(
        "workspace-1",
        "endpoint-1",
        "https://example.test/hook",
    )

    store.record_delivery(
        "workspace-1",
        "endpoint-1",
        "delivery-1",
        "delivered",
        {
            "event": "run.completed",
            "internal_run_id": "run-secret",
            "data": {"ok": True, "_trace_id": "trace-secret"},
        },
        sequence=1,
    )

    status = store.get_last_status("workspace-1", "endpoint-1")
    assert status["status"] == "delivered"
    assert status["payload"] == {
        "event": "run.completed",
        "data": {"ok": True},
    }


def test_rejects_disabled_endpoint_before_recording_success():
    store = DeliveryAuditStore()
    store.register_endpoint(
        "workspace-1",
        "endpoint-1",
        "https://example.test/hook",
    )
    assert store.disable_endpoint("workspace-1", "endpoint-1")

    with pytest.raises(DeliveryRejected):
        store.record_delivery(
            "workspace-1",
            "endpoint-1",
            "delivery-1",
            "delivered",
            {"event": "run.completed"},
            sequence=1,
        )

    assert store.get_last_status("workspace-1", "endpoint-1") is None


def test_rejects_non_object_payload_before_recording_success():
    store = DeliveryAuditStore()
    store.register_endpoint(
        "workspace-1",
        "endpoint-1",
        "https://example.test/hook",
    )

    with pytest.raises(DeliveryRejected):
        store.record_delivery(
            "workspace-1",
            "endpoint-1",
            "delivery-1",
            "delivered",
            ["not", "an", "object"],
            sequence=1,
        )

    assert store.get_last_status("workspace-1", "endpoint-1") is None


def test_retry_does_not_let_stale_result_overwrite_newer_status():
    store = DeliveryAuditStore()
    store.register_endpoint(
        "workspace-1",
        "endpoint-1",
        "https://example.test/hook",
    )

    store.record_delivery(
        "workspace-1",
        "endpoint-1",
        "delivery-1",
        "failed",
        {"event": "run.completed"},
        sequence=10,
        attempt=1,
        delivered_at=20.0,
    )
    store.record_delivery(
        "workspace-1",
        "endpoint-1",
        "delivery-1",
        "delivered",
        {"event": "run.completed"},
        sequence=10,
        attempt=2,
        delivered_at=21.0,
    )
    store.record_delivery(
        "workspace-1",
        "endpoint-1",
        "delivery-1",
        "failed",
        {"event": "run.completed"},
        sequence=9,
        attempt=99,
        delivered_at=30.0,
    )

    status = store.get_delivery("workspace-1", "endpoint-1", "delivery-1")
    assert status["status"] == "delivered"
    assert status["attempt"] == 2


def test_workspace_isolation_rejects_cross_workspace_delivery():
    store = DeliveryAuditStore()
    store.register_endpoint(
        "workspace-1",
        "endpoint-1",
        "https://example.test/hook",
    )

    with pytest.raises(DeliveryRejected):
        store.record_delivery(
            "workspace-2",
            "endpoint-1",
            "delivery-1",
            "delivered",
            {"event": "run.completed"},
            sequence=1,
        )

    store.record_delivery(
        "workspace-1",
        "endpoint-1",
        "delivery-1",
        "delivered",
        {"event": "run.completed"},
        sequence=1,
    )

    assert store.get_last_status("workspace-2", "endpoint-1") is None
    assert (
        store.get_last_status("workspace-1", "endpoint-1")["status"]
        == "delivered"
    )
