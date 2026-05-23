from src.api.webhooks import (
    WebhookDeliveryError,
    WebhookDeliveryService,
    shape_public_payload,
)


def test_shape_public_payload_strips_nested_internal_metadata():
    payload = {
        "event_id": "evt-1",
        "workspace_id": "workspace-a",
        "event_type": "run.completed",
        "data": {
            "public_result": "ok",
            "internal_run_id": "run-secret",
            "nested": {"trace_id": "trace-secret", "visible": True},
            "items": [{"token": "hidden", "name": "kept"}],
        },
        "_debug": {"stack": "hidden"},
    }

    shaped = shape_public_payload(payload)

    assert shaped == {
        "event_id": "evt-1",
        "workspace_id": "workspace-a",
        "event_type": "run.completed",
        "data": {
            "public_result": "ok",
            "nested": {"visible": True},
            "items": [{"name": "kept"}],
        },
    }


def test_valid_delivery_records_only_public_payload():
    service = WebhookDeliveryService()
    endpoint = service.register_endpoint(
        "workspace-a",
        "run.completed",
        "https://example.test/webhooks/runs",
    )
    delivered_payloads = []

    def sender(_endpoint, payload):
        delivered_payloads.append(payload)
        return 204

    record = service.deliver_to_endpoint(
        endpoint.id,
        {
            "event_id": "evt-1",
            "workspace_id": "workspace-a",
            "event_type": "run.completed",
            "data": {"result": "ok", "run_metadata": {"host": "internal"}},
            "trace_id": "trace-secret",
        },
        sender,
    )

    assert record.status == "delivered"
    assert record.attempts == 1
    assert delivered_payloads == [record.public_payload]
    assert record.public_payload == {
        "event_id": "evt-1",
        "workspace_id": "workspace-a",
        "event_type": "run.completed",
        "data": {"result": "ok"},
    }


def test_rejected_delivery_does_not_call_endpoint():
    service = WebhookDeliveryService()
    endpoint = service.register_endpoint(
        "workspace-a",
        "run.completed",
        "https://example.test/webhooks/runs",
    )
    service.disable_endpoint(endpoint.id, "rotated")
    calls = []

    record = service.deliver_to_endpoint(
        endpoint.id,
        {
            "event_id": "evt-2",
            "workspace_id": "workspace-a",
            "event_type": "run.completed",
            "internal_run_id": "hidden",
        },
        lambda *_args: calls.append("called") or 204,
    )

    assert record.status == "rejected"
    assert record.reason == "rotated"
    assert record.attempts == 0
    assert calls == []
    assert "internal_run_id" not in record.public_payload


def test_retry_updates_same_record_without_duplicate_success_callback():
    service = WebhookDeliveryService()
    endpoint = service.register_endpoint(
        "workspace-a",
        "run.completed",
        "https://example.test/webhooks/runs",
    )
    responses = [503, 200]
    seen_payloads = []

    def sender(_endpoint, payload):
        seen_payloads.append(payload)
        return responses.pop(0)

    event = {
        "event_id": "evt-3",
        "workspace_id": "workspace-a",
        "event_type": "run.completed",
        "data": {"result": "ok", "authorization": "hidden"},
    }

    first = service.deliver_to_endpoint(endpoint.id, event, sender)
    second_without_retry = service.deliver_to_endpoint(
        endpoint.id,
        event,
        sender,
    )
    retry = service.deliver_to_endpoint(
        endpoint.id,
        event,
        sender,
        retry=True,
    )
    duplicate = service.deliver_to_endpoint(
        endpoint.id,
        event,
        sender,
        retry=True,
    )

    assert first.status == "failed"
    assert second_without_retry is first
    assert retry.status == "delivered"
    assert retry.id == first.id
    assert retry.attempts == 2
    assert duplicate is retry
    assert len(seen_payloads) == 2
    assert all(
        payload["data"] == {"result": "ok"}
        for payload in seen_payloads
    )


def test_workspace_isolation_rejects_cross_workspace_event():
    service = WebhookDeliveryService()
    endpoint = service.register_endpoint(
        "workspace-a",
        "run.completed",
        "https://example.test/webhooks/runs",
    )
    calls = []

    record = service.deliver_to_endpoint(
        endpoint.id,
        {
            "event_id": "evt-4",
            "workspace_id": "workspace-b",
            "event_type": "run.completed",
            "data": {"result": "ok"},
        },
        lambda *_args: calls.append("called") or 204,
    )

    assert record.status == "rejected"
    assert record.reason == "workspace_mismatch"
    assert calls == []


def test_http_410_disables_endpoint_and_blocks_future_callbacks():
    service = WebhookDeliveryService()
    endpoint = service.register_endpoint(
        "workspace-a",
        "run.completed",
        "https://example.test/webhooks/runs",
    )
    calls = []

    def sender(_endpoint, _payload):
        calls.append("called")
        return 410

    first = service.deliver_to_endpoint(
        endpoint.id,
        {
            "event_id": "evt-5",
            "workspace_id": "workspace-a",
            "event_type": "run.completed",
            "private": {"reason": "hidden"},
        },
        sender,
    )
    second = service.deliver_to_endpoint(
        endpoint.id,
        {
            "event_id": "evt-6",
            "workspace_id": "workspace-a",
            "event_type": "run.completed",
        },
        sender,
    )

    assert first.status == "gone"
    assert first.reason == "http_410_gone"
    assert not endpoint.enabled
    assert endpoint.disabled_reason == "http_410_gone"
    assert second.status == "rejected"
    assert second.reason == "http_410_gone"
    assert calls == ["called"]


def test_endpoint_registration_rejects_unscoped_targets():
    service = WebhookDeliveryService()

    try:
        service.register_endpoint(
            "workspace-a",
            "run.completed",
            "file:///tmp/out",
        )
    except WebhookDeliveryError as exc:
        assert "target_url" in str(exc)
    else:
        raise AssertionError("invalid webhook target was accepted")
