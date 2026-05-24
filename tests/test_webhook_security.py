import time

import pytest
from fastapi.testclient import TestClient

from src.api import routes
from src.api.server import create_app
from src.api.webhooks import (
    WebhookReplayStore,
    WebhookSubscription,
    WebhookVerificationError,
    WebhookVerifier,
    sign_webhook_payload,
)


def _headers(secret, timestamp, body, delivery_id="delivery-1"):
    return {
        "X-AO-Delivery-ID": delivery_id,
        "X-AO-Timestamp": str(timestamp),
        "X-AO-Signature": sign_webhook_payload(secret, timestamp, body),
    }


def test_valid_webhook_signature_returns_bounded_delivery_record():
    subscription = WebhookSubscription(
        workspace_id="workspace-a",
        endpoint_id="endpoint-a",
        secret="secret-a",
    )
    body = b'{"event":"task.completed","payload":{"private":"value"}}'
    verifier = WebhookVerifier()

    record = verifier.verify(
        subscription,
        body,
        _headers("secret-a", 1_000, body),
        now=1_020,
    )

    assert record.workspace_id == "workspace-a"
    assert record.endpoint_id == "endpoint-a"
    assert record.delivery_id == "delivery-1"
    assert record.accepted_at == 1_020
    assert record.duplicate is False
    assert "private" not in record.idempotency_key


def test_rejects_webhook_outside_replay_window_before_replay_recording():
    subscription = WebhookSubscription(
        workspace_id="workspace-a",
        endpoint_id="endpoint-a",
        secret="secret-a",
        replay_window_seconds=300,
    )
    body = b'{"event":"task.completed"}'
    verifier = WebhookVerifier()

    with pytest.raises(WebhookVerificationError, match="replay window"):
        verifier.verify(
            subscription,
            body,
            _headers("secret-a", 1_000, body),
            now=1_301,
        )


def test_rejects_replayed_signature_for_new_delivery_id():
    subscription = WebhookSubscription(
        workspace_id="workspace-a",
        endpoint_id="endpoint-a",
        secret="secret-a",
    )
    body = b'{"event":"task.completed"}'
    headers = _headers("secret-a", 2_000, body)
    verifier = WebhookVerifier()

    verifier.verify(subscription, body, headers, now=2_005)
    replay_headers = dict(headers)
    replay_headers["X-AO-Delivery-ID"] = "delivery-2"

    with pytest.raises(WebhookVerificationError, match="already been used"):
        verifier.verify(subscription, body, replay_headers, now=2_006)


def test_duplicate_delivery_id_is_idempotent_for_exact_retry():
    subscription = WebhookSubscription(
        workspace_id="workspace-a",
        endpoint_id="endpoint-a",
        secret="secret-a",
    )
    body = b'{"event":"task.completed"}'
    headers = _headers("secret-a", 3_000, body)
    verifier = WebhookVerifier()

    first = verifier.verify(subscription, body, headers, now=3_001)
    duplicate = verifier.verify(subscription, body, headers, now=3_002)

    assert duplicate.duplicate is True
    assert duplicate.idempotency_key == first.idempotency_key
    assert duplicate.accepted_at == first.accepted_at


def test_delivery_id_reuse_with_different_signature_is_rejected():
    subscription = WebhookSubscription(
        workspace_id="workspace-a",
        endpoint_id="endpoint-a",
        secret="secret-a",
    )
    first_body = b'{"event":"task.completed","n":1}'
    second_body = b'{"event":"task.completed","n":2}'
    verifier = WebhookVerifier()

    verifier.verify(
        subscription,
        first_body,
        _headers("secret-a", 4_000, first_body),
        now=4_001,
    )

    with pytest.raises(WebhookVerificationError, match="reused"):
        verifier.verify(
            subscription,
            second_body,
            _headers("secret-a", 4_001, second_body),
            now=4_002,
        )


def test_replay_state_is_scoped_by_workspace_and_endpoint():
    store = WebhookReplayStore()
    verifier = WebhookVerifier(store)
    body = b'{"event":"task.completed"}'
    first = WebhookSubscription(
        workspace_id="workspace-a",
        endpoint_id="endpoint-a",
        secret="shared-secret",
    )
    second = WebhookSubscription(
        workspace_id="workspace-b",
        endpoint_id="endpoint-a",
        secret="shared-secret",
    )
    headers = _headers("shared-secret", 5_000, body)

    first_record = verifier.verify(first, body, headers, now=5_001)
    second_record = verifier.verify(second, body, headers, now=5_002)

    assert first_record.workspace_id == "workspace-a"
    assert second_record.workspace_id == "workspace-b"
    assert first_record.idempotency_key != second_record.idempotency_key


def test_rejects_invalid_signature_without_recording_delivery():
    subscription = WebhookSubscription(
        workspace_id="workspace-a",
        endpoint_id="endpoint-a",
        secret="secret-a",
    )
    body = b'{"event":"task.completed"}'
    headers = _headers("secret-a", 6_000, body)
    headers["X-AO-Signature"] = "sha256=bad"
    verifier = WebhookVerifier()

    with pytest.raises(WebhookVerificationError, match="invalid"):
        verifier.verify(subscription, body, headers, now=6_001)

    good_record = verifier.verify(
        subscription,
        body,
        _headers("secret-a", 6_000, body),
        now=6_002,
    )
    assert good_record.duplicate is False


def test_webhook_route_verifies_before_accepting_event():
    routes.webhook_subscriptions.clear()
    routes.webhook_verifier = WebhookVerifier()
    subscription = WebhookSubscription(
        workspace_id="workspace-a",
        endpoint_id="endpoint-a",
        secret="secret-a",
    )
    routes.register_webhook_subscription(subscription)
    app = create_app()
    client = TestClient(app)
    body = b'{"event":"task.completed","payload":{"private":"value"}}'
    headers = _headers("secret-a", int(time.time()), body)

    response = client.post(
        "/api/v2/webhooks/workspace-a/endpoint-a/events",
        content=body,
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    assert data["workspace_id"] == "workspace-a"
    assert data["endpoint_id"] == "endpoint-a"
    assert "payload" not in data


def test_webhook_route_rejects_unregistered_workspace_endpoint():
    routes.webhook_subscriptions.clear()
    routes.webhook_verifier = WebhookVerifier()
    app = create_app()
    client = TestClient(app)
    body = b'{"event":"task.completed"}'
    headers = _headers("secret-a", int(time.time()), body)

    response = client.post(
        "/api/v2/webhooks/workspace-a/endpoint-a/events",
        content=body,
        headers=headers,
    )

    assert response.status_code == 404
