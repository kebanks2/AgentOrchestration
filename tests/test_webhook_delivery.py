import asyncio
import socket
import time

import pytest

from src.api.webhook_delivery import (
    InMemoryWebhookDeliveryStore,
    WebhookDeliveryError,
    WebhookDeliveryRuntime,
    WebhookEndpoint,
)


class RecordingResolver:
    def __init__(self, *, fail=False, address="93.184.216.34"):
        self.fail = fail
        self.address = address
        self.calls = []

    async def __call__(self, host, port):
        self.calls.append((host, port))
        if self.fail:
            raise socket.gaierror("name or service not known")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (self.address, port))
        ]


class RecordingSender:
    def __init__(self, status_code=204):
        self.status_code = status_code
        self.calls = []

    async def __call__(self, endpoint, event, payload, delivery_id):
        self.calls.append((endpoint, event, dict(payload), delivery_id))
        return self.status_code


def run(coro):
    return asyncio.run(coro)


def test_valid_delivery_records_sanitized_public_result():
    resolver = RecordingResolver()
    sender = RecordingSender()
    runtime = WebhookDeliveryRuntime(resolver=resolver, sender=sender)

    async def scenario():
        endpoint = await runtime.register_endpoint(
            "acme",
            "https://hooks.example.test/agent",
            ["agent.started"],
            endpoint_id="hook-1",
        )
        return await runtime.deliver(
            "acme",
            endpoint.endpoint_id,
            "agent.started",
            {"agent_id": "agent-1"},
            delivery_id="delivery-1",
        )

    record = run(scenario())

    assert record.status == "delivered"
    assert record.response_status == 204
    assert len(sender.calls) == 1
    assert sender.calls[0][1:] == (
        "agent.started",
        {"agent_id": "agent-1"},
        "delivery-1",
    )
    assert record.to_public_dict() == {
        "delivery_id": "delivery-1",
        "endpoint_id": "hook-1",
        "workspace_id": "acme",
        "event": "agent.started",
        "status": "delivered",
        "attempts": 1,
        "response_status": 204,
    }
    assert "internal_error" not in record.to_public_dict()
    assert "target_url" not in record.to_public_dict()


def test_dns_failure_rejects_endpoint_before_persistence():
    runtime = WebhookDeliveryRuntime(resolver=RecordingResolver(fail=True))

    async def scenario():
        with pytest.raises(
            WebhookDeliveryError,
            match="DNS resolution failed",
        ):
            await runtime.register_endpoint(
                "acme",
                "https://missing.example.test/hook",
                ["agent.started"],
            )

    run(scenario())

    assert runtime.store.endpoint_count() == 0


def test_non_public_dns_result_rejects_endpoint_before_persistence():
    runtime = WebhookDeliveryRuntime(
        resolver=RecordingResolver(address="127.0.0.1")
    )

    async def scenario():
        with pytest.raises(WebhookDeliveryError, match="non-public address"):
            await runtime.register_endpoint(
                "acme",
                "https://localhost.example.test/hook",
                ["agent.started"],
            )

    run(scenario())

    assert runtime.store.endpoint_count() == 0


def test_credentialed_url_rejects_endpoint_before_dns_lookup():
    resolver = RecordingResolver()
    runtime = WebhookDeliveryRuntime(resolver=resolver)

    async def scenario():
        with pytest.raises(WebhookDeliveryError, match="must not include"):
            await runtime.register_endpoint(
                "acme",
                "https://user:secret@hooks.example.test/hook",
                ["agent.started"],
            )

    run(scenario())

    assert resolver.calls == []
    assert runtime.store.endpoint_count() == 0


def test_dns_failure_during_delivery_is_idempotent_and_skips_send():
    store = InMemoryWebhookDeliveryStore()
    store.add_endpoint(
        WebhookEndpoint(
            endpoint_id="hook-1",
            workspace_id="acme",
            target_url="https://missing.example.test/hook",
            events=frozenset({"agent.started"}),
        )
    )
    resolver = RecordingResolver(fail=True)
    sender = RecordingSender()
    runtime = WebhookDeliveryRuntime(
        store=store,
        resolver=resolver,
        sender=sender,
    )

    async def scenario():
        first = await runtime.deliver(
            "acme",
            "hook-1",
            "agent.started",
            {"agent_id": "agent-1"},
            delivery_id="delivery-1",
        )
        second = await runtime.deliver(
            "acme",
            "hook-1",
            "agent.started",
            {"agent_id": "agent-1"},
            delivery_id="delivery-1",
        )
        return first, second

    first, second = run(scenario())

    assert first.status == "failed"
    assert first.internal_error is not None
    assert first.to_public_dict()["status"] == "failed"
    assert "internal_error" not in first.to_public_dict()
    assert second == first
    assert len(resolver.calls) == 1
    assert sender.calls == []


def test_dns_timeout_fails_fast_without_dispatching():
    async def slow_resolver(host, port):
        await asyncio.sleep(60)

    store = InMemoryWebhookDeliveryStore()
    store.add_endpoint(
        WebhookEndpoint(
            endpoint_id="hook-1",
            workspace_id="acme",
            target_url="https://slow.example.test/hook",
            events=frozenset({"agent.started"}),
        )
    )
    sender = RecordingSender()
    runtime = WebhookDeliveryRuntime(
        store=store,
        resolver=slow_resolver,
        sender=sender,
        dns_timeout=0.01,
    )

    start = time.monotonic()
    record = run(
        runtime.deliver(
            "acme",
            "hook-1",
            "agent.started",
            {"agent_id": "agent-1"},
            delivery_id="delivery-1",
        )
    )

    assert time.monotonic() - start < 0.5
    assert record.status == "failed"
    assert "timed out" in record.internal_error
    assert sender.calls == []


def test_workspace_scope_and_disabled_endpoints_fail_closed():
    resolver = RecordingResolver()
    sender = RecordingSender()
    runtime = WebhookDeliveryRuntime(resolver=resolver, sender=sender)

    async def scenario():
        endpoint = await runtime.register_endpoint(
            "acme",
            "https://hooks.example.test/agent",
            ["agent.started"],
            endpoint_id="hook-1",
        )
        with pytest.raises(
            WebhookDeliveryError,
            match="not found for workspace",
        ):
            await runtime.deliver(
                "other",
                endpoint.endpoint_id,
                "agent.started",
                {"agent_id": "agent-1"},
                delivery_id="delivery-1",
            )
        runtime.disable_endpoint("acme", endpoint.endpoint_id)
        with pytest.raises(WebhookDeliveryError, match="disabled"):
            await runtime.deliver(
                "acme",
                endpoint.endpoint_id,
                "agent.started",
                {"agent_id": "agent-1"},
                delivery_id="delivery-2",
            )

    run(scenario())

    assert sender.calls == []


def test_rotated_endpoint_disables_old_target_and_allows_replacement():
    resolver = RecordingResolver()
    sender = RecordingSender()
    runtime = WebhookDeliveryRuntime(resolver=resolver, sender=sender)

    async def scenario():
        endpoint = await runtime.register_endpoint(
            "acme",
            "https://old-hooks.example.test/agent",
            ["agent.started"],
            endpoint_id="hook-1",
        )
        replacement = await runtime.rotate_endpoint(
            "acme",
            endpoint.endpoint_id,
            "https://new-hooks.example.test/agent",
        )
        with pytest.raises(WebhookDeliveryError, match="disabled"):
            await runtime.deliver(
                "acme",
                endpoint.endpoint_id,
                "agent.started",
                {"agent_id": "agent-1"},
                delivery_id="delivery-old",
            )
        return await runtime.deliver(
            "acme",
            replacement.endpoint_id,
            "agent.started",
            {"agent_id": "agent-1"},
            delivery_id="delivery-new",
        )

    record = run(scenario())

    assert record.status == "delivered"
    assert len(sender.calls) == 1
