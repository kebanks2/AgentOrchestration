"""Webhook delivery runtime guardrails."""

import asyncio
import ipaddress
import socket
import uuid
from dataclasses import dataclass, replace
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    Mapping,
    Optional,
    Tuple,
)
from urllib.parse import urlparse

import httpx


Resolver = Callable[[str, int], Awaitable[Iterable[Any]]]
Sender = Callable[
    ["WebhookEndpoint", str, Mapping[str, Any], str],
    Awaitable[int],
]


class WebhookDeliveryError(ValueError):
    """Raised when webhook delivery cannot proceed safely."""


@dataclass(frozen=True)
class WebhookEndpoint:
    endpoint_id: str
    workspace_id: str
    target_url: str
    events: frozenset[str]
    enabled: bool = True
    generation: int = 1


@dataclass(frozen=True)
class WebhookDeliveryRecord:
    delivery_id: str
    endpoint_id: str
    workspace_id: str
    event: str
    status: str
    attempts: int
    response_status: Optional[int] = None
    internal_error: Optional[str] = None

    def to_public_dict(self) -> Dict[str, Any]:
        """Return callback-safe delivery data without internals."""
        payload: Dict[str, Any] = {
            "delivery_id": self.delivery_id,
            "endpoint_id": self.endpoint_id,
            "workspace_id": self.workspace_id,
            "event": self.event,
            "status": self.status,
            "attempts": self.attempts,
        }
        if self.response_status is not None:
            payload["response_status"] = self.response_status
        return payload


class InMemoryWebhookDeliveryStore:
    """Small storage boundary used by the delivery runtime and tests."""

    def __init__(self) -> None:
        self._endpoints: Dict[Tuple[str, str], WebhookEndpoint] = {}
        self._records: Dict[Tuple[str, str, str], WebhookDeliveryRecord] = {}

    def add_endpoint(self, endpoint: WebhookEndpoint) -> None:
        key = (endpoint.workspace_id, endpoint.endpoint_id)
        self._endpoints[key] = endpoint

    def get_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
    ) -> Optional[WebhookEndpoint]:
        return self._endpoints.get((workspace_id, endpoint_id))

    def update_endpoint(self, endpoint: WebhookEndpoint) -> None:
        key = (endpoint.workspace_id, endpoint.endpoint_id)
        if key not in self._endpoints:
            raise WebhookDeliveryError(
                "webhook endpoint not found for workspace"
            )
        self._endpoints[key] = endpoint

    def endpoint_count(self) -> int:
        return len(self._endpoints)

    def get_record(
        self,
        workspace_id: str,
        endpoint_id: str,
        delivery_id: str,
    ) -> Optional[WebhookDeliveryRecord]:
        return self._records.get((workspace_id, endpoint_id, delivery_id))

    def save_record(self, record: WebhookDeliveryRecord) -> None:
        key = (record.workspace_id, record.endpoint_id, record.delivery_id)
        self._records[key] = record


class WebhookDeliveryRuntime:
    """Validates and delivers webhooks without blocking worker progress."""

    def __init__(
        self,
        *,
        store: Optional[InMemoryWebhookDeliveryStore] = None,
        resolver: Optional[Resolver] = None,
        sender: Optional[Sender] = None,
        dns_timeout: float = 2.0,
    ) -> None:
        self.store = store or InMemoryWebhookDeliveryStore()
        self._resolver = resolver or self._default_resolve
        self._sender = sender or self._default_send
        self._dns_timeout = dns_timeout

    async def register_endpoint(
        self,
        workspace_id: str,
        target_url: str,
        events: Iterable[str],
        *,
        endpoint_id: Optional[str] = None,
    ) -> WebhookEndpoint:
        subscribed_events = frozenset(event for event in events if event)
        if not subscribed_events:
            raise WebhookDeliveryError(
                "webhook endpoint requires at least one event"
            )

        await self._validate_dns(target_url)
        endpoint = WebhookEndpoint(
            endpoint_id=endpoint_id or uuid.uuid4().hex,
            workspace_id=workspace_id,
            target_url=target_url,
            events=subscribed_events,
        )
        self.store.add_endpoint(endpoint)
        return endpoint

    async def rotate_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
        target_url: str,
    ) -> WebhookEndpoint:
        endpoint = self._require_endpoint(workspace_id, endpoint_id)
        await self._validate_dns(target_url)

        disabled = replace(endpoint, enabled=False)
        replacement = replace(
            endpoint,
            endpoint_id=uuid.uuid4().hex,
            target_url=target_url,
            enabled=True,
            generation=endpoint.generation + 1,
        )
        self.store.update_endpoint(disabled)
        self.store.add_endpoint(replacement)
        return replacement

    def disable_endpoint(self, workspace_id: str, endpoint_id: str) -> None:
        endpoint = self._require_endpoint(workspace_id, endpoint_id)
        self.store.update_endpoint(replace(endpoint, enabled=False))

    async def deliver(
        self,
        workspace_id: str,
        endpoint_id: str,
        event: str,
        payload: Mapping[str, Any],
        *,
        delivery_id: str,
    ) -> WebhookDeliveryRecord:
        existing = self.store.get_record(
            workspace_id,
            endpoint_id,
            delivery_id,
        )
        if existing:
            return existing

        endpoint = self._require_endpoint(workspace_id, endpoint_id)
        if not endpoint.enabled:
            raise WebhookDeliveryError("webhook endpoint is disabled")
        if event not in endpoint.events and "*" not in endpoint.events:
            raise WebhookDeliveryError(
                "webhook endpoint is not subscribed to event"
            )

        try:
            await self._validate_dns(endpoint.target_url)
            response_status = await self._sender(
                endpoint,
                event,
                payload,
                delivery_id,
            )
        except WebhookDeliveryError as exc:
            record = WebhookDeliveryRecord(
                delivery_id=delivery_id,
                endpoint_id=endpoint.endpoint_id,
                workspace_id=endpoint.workspace_id,
                event=event,
                status="failed",
                attempts=1,
                internal_error=str(exc),
            )
            self.store.save_record(record)
            return record
        except Exception as exc:
            # Defensive boundary for injected senders.
            record = WebhookDeliveryRecord(
                delivery_id=delivery_id,
                endpoint_id=endpoint.endpoint_id,
                workspace_id=endpoint.workspace_id,
                event=event,
                status="failed",
                attempts=1,
                internal_error=f"webhook delivery failed: {exc}",
            )
            self.store.save_record(record)
            return record

        status = "delivered" if 200 <= response_status < 300 else "failed"
        record = WebhookDeliveryRecord(
            delivery_id=delivery_id,
            endpoint_id=endpoint.endpoint_id,
            workspace_id=endpoint.workspace_id,
            event=event,
            status=status,
            attempts=1,
            response_status=response_status,
            internal_error=(
                None if status == "delivered" else f"HTTP {response_status}"
            ),
        )
        self.store.save_record(record)
        return record

    def _require_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
    ) -> WebhookEndpoint:
        endpoint = self.store.get_endpoint(workspace_id, endpoint_id)
        if not endpoint:
            raise WebhookDeliveryError(
                "webhook endpoint not found for workspace"
            )
        return endpoint

    async def _validate_dns(self, target_url: str) -> None:
        host, port = self._parse_target(target_url)
        try:
            addresses = await asyncio.wait_for(
                self._resolver(host, port),
                timeout=self._dns_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise WebhookDeliveryError(
                f"webhook endpoint DNS resolution timed out for {host}"
            ) from exc
        except OSError as exc:
            raise WebhookDeliveryError(
                f"webhook endpoint DNS resolution failed for {host}"
            ) from exc

        resolved_addresses = list(addresses)
        if not resolved_addresses:
            raise WebhookDeliveryError(
                "webhook endpoint DNS resolution returned no addresses "
                f"for {host}"
            )
        self._validate_public_addresses(host, resolved_addresses)

    @staticmethod
    def _validate_public_addresses(
        host: str,
        addresses: Iterable[Any],
    ) -> None:
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address[-1][0])
            except (IndexError, TypeError, ValueError) as exc:
                raise WebhookDeliveryError(
                    "webhook endpoint DNS returned an invalid address "
                    f"for {host}"
                ) from exc
            if not ip.is_global:
                raise WebhookDeliveryError(
                    "webhook endpoint DNS returned a non-public address "
                    f"for {host}"
                )

    @staticmethod
    def _parse_target(target_url: str) -> Tuple[str, int]:
        try:
            parsed = urlparse(target_url)
            port = parsed.port
        except ValueError as exc:
            raise WebhookDeliveryError(
                "webhook endpoint URL is invalid"
            ) from exc

        if parsed.scheme not in {"http", "https"}:
            raise WebhookDeliveryError(
                "webhook endpoint URL must use http or https"
            )
        if parsed.username or parsed.password:
            raise WebhookDeliveryError(
                "webhook endpoint URL must not include credentials"
            )
        if not parsed.hostname:
            raise WebhookDeliveryError(
                "webhook endpoint URL must include a host"
            )
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        return parsed.hostname, port

    @staticmethod
    async def _default_resolve(host: str, port: int) -> Iterable[Any]:
        return await asyncio.to_thread(
            socket.getaddrinfo,
            host,
            port,
            type=socket.SOCK_STREAM,
        )

    @staticmethod
    async def _default_send(
        endpoint: WebhookEndpoint,
        event: str,
        payload: Mapping[str, Any],
        delivery_id: str,
    ) -> int:
        headers = {
            "x-ao-event": event,
            "x-ao-delivery-id": delivery_id,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                endpoint.target_url,
                json=payload,
                headers=headers,
            )
        return response.status_code
