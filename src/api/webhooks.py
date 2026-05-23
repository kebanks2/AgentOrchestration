"""Webhook endpoint registration and safe public payload delivery."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse


INTERNAL_ONLY_FIELDS = {
    "agent_internal_id",
    "authorization",
    "credentials",
    "debug",
    "internal",
    "internal_metadata",
    "internal_run_id",
    "principal",
    "private",
    "retry_state",
    "run_metadata",
    "secret",
    "stack_trace",
    "task_internal_id",
    "token",
    "trace_id",
    "workspace_internal_id",
}


TERMINAL_STATUSES = {"delivered", "rejected", "gone"}


class WebhookDeliveryError(ValueError):
    """Raised when a webhook endpoint or event cannot be delivered safely."""


@dataclass
class WebhookEndpoint:
    id: str
    workspace_id: str
    event_type: str
    target_url: str
    enabled: bool = True
    generation: int = 1
    disabled_reason: Optional[str] = None
    created_at: float = field(default_factory=time.time)


@dataclass
class WebhookDeliveryRecord:
    id: str
    endpoint_id: str
    workspace_id: str
    event_id: str
    event_type: str
    status: str
    attempts: int
    public_payload: Dict[str, Any]
    response_status: Optional[int] = None
    reason: Optional[str] = None
    updated_at: float = field(default_factory=time.time)


def _is_internal_field(key: str) -> bool:
    normalized = key.lower()
    return (
        normalized in INTERNAL_ONLY_FIELDS
        or normalized.startswith("_")
        or normalized.endswith("_token")
        or normalized.endswith("_secret")
        or "internal_" in normalized
    )


def shape_public_payload(payload: Any) -> Any:
    """Return a JSON-compatible payload with internal-only fields removed."""
    if isinstance(payload, dict):
        return {
            key: shape_public_payload(value)
            for key, value in payload.items()
            if not _is_internal_field(str(key))
        }
    if isinstance(payload, list):
        return [shape_public_payload(item) for item in payload]
    return payload


def _validate_target_url(target_url: str) -> str:
    parsed = urlparse(target_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WebhookDeliveryError(
            "webhook target_url must be an absolute http(s) URL",
        )
    return target_url


class WebhookDeliveryService:
    """In-memory webhook registry with scoped, idempotent public delivery."""

    def __init__(self):
        self._endpoints: Dict[str, WebhookEndpoint] = {}
        self._delivery_records: Dict[str, WebhookDeliveryRecord] = {}

    def register_endpoint(
        self,
        workspace_id: str,
        event_type: str,
        target_url: str,
    ) -> WebhookEndpoint:
        if not workspace_id.strip():
            raise WebhookDeliveryError("workspace_id is required")
        if not event_type.strip():
            raise WebhookDeliveryError("event_type is required")

        endpoint = WebhookEndpoint(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            event_type=event_type,
            target_url=_validate_target_url(target_url),
        )
        self._endpoints[endpoint.id] = endpoint
        return endpoint

    def disable_endpoint(
        self,
        endpoint_id: str,
        reason: str,
    ) -> WebhookEndpoint:
        endpoint = self._require_endpoint(endpoint_id)
        endpoint.enabled = False
        endpoint.disabled_reason = reason
        return endpoint

    def rotate_endpoint(
        self,
        endpoint_id: str,
        target_url: str,
    ) -> WebhookEndpoint:
        old_endpoint = self.disable_endpoint(endpoint_id, "rotated")
        endpoint = WebhookEndpoint(
            id=str(uuid.uuid4()),
            workspace_id=old_endpoint.workspace_id,
            event_type=old_endpoint.event_type,
            target_url=_validate_target_url(target_url),
            generation=old_endpoint.generation + 1,
        )
        self._endpoints[endpoint.id] = endpoint
        return endpoint

    def list_endpoints(self, workspace_id: str) -> List[WebhookEndpoint]:
        return [
            endpoint
            for endpoint in self._endpoints.values()
            if endpoint.workspace_id == workspace_id
        ]

    def deliver_to_endpoint(
        self,
        endpoint_id: str,
        event: Dict[str, Any],
        sender: Callable[[WebhookEndpoint, Dict[str, Any]], int],
        *,
        retry: bool = False,
    ) -> WebhookDeliveryRecord:
        endpoint = self._require_endpoint(endpoint_id)
        event_id = str(event.get("event_id") or event.get("id") or "")
        event_type = str(event.get("event_type") or event.get("type") or "")
        workspace_id = str(event.get("workspace_id") or "")

        if not event_id:
            return self._record_rejection(endpoint, event, "missing_event_id")
        if workspace_id != endpoint.workspace_id:
            return self._record_rejection(
                endpoint,
                event,
                "workspace_mismatch",
            )
        if event_type != endpoint.event_type:
            return self._record_rejection(
                endpoint,
                event,
                "event_type_mismatch",
            )
        if not endpoint.enabled:
            return self._record_rejection(
                endpoint,
                event,
                endpoint.disabled_reason or "disabled",
            )

        delivery_id = self._delivery_id(endpoint.id, event_id)
        existing = self._delivery_records.get(delivery_id)
        if existing and (existing.status in TERMINAL_STATUSES or not retry):
            return existing

        payload = self._public_event(event)
        attempts = (existing.attempts if existing else 0) + 1
        response_status = sender(endpoint, payload)
        status = "delivered" if 200 <= response_status < 300 else "failed"
        reason = None

        if response_status == 410:
            endpoint.enabled = False
            endpoint.disabled_reason = "http_410_gone"
            status = "gone"
            reason = "http_410_gone"

        record = WebhookDeliveryRecord(
            id=delivery_id,
            endpoint_id=endpoint.id,
            workspace_id=endpoint.workspace_id,
            event_id=event_id,
            event_type=event_type,
            status=status,
            attempts=attempts,
            public_payload=payload,
            response_status=response_status,
            reason=reason,
        )
        self._delivery_records[delivery_id] = record
        return record

    def deliver_event(
        self,
        event: Dict[str, Any],
        sender: Callable[[WebhookEndpoint, Dict[str, Any]], int],
        *,
        retry: bool = False,
    ) -> List[WebhookDeliveryRecord]:
        workspace_id = str(event.get("workspace_id") or "")
        event_type = str(event.get("event_type") or event.get("type") or "")
        endpoints = [
            endpoint
            for endpoint in self._endpoints.values()
            if (
                endpoint.workspace_id == workspace_id
                and endpoint.event_type == event_type
            )
        ]
        return [
            self.deliver_to_endpoint(endpoint.id, event, sender, retry=retry)
            for endpoint in endpoints
        ]

    def get_delivery_record(
        self,
        endpoint_id: str,
        event_id: str,
    ) -> Optional[WebhookDeliveryRecord]:
        return self._delivery_records.get(
            self._delivery_id(endpoint_id, event_id),
        )

    def _record_rejection(
        self,
        endpoint: WebhookEndpoint,
        event: Dict[str, Any],
        reason: str,
    ) -> WebhookDeliveryRecord:
        event_id = str(
            event.get("event_id") or event.get("id") or str(uuid.uuid4()),
        )
        event_type = str(event.get("event_type") or event.get("type") or "")
        if not event_type:
            event_type = endpoint.event_type
        workspace_id = str(event.get("workspace_id") or "")
        delivery_id = self._delivery_id(endpoint.id, event_id)
        existing = self._delivery_records.get(delivery_id)
        if existing:
            return existing

        record = WebhookDeliveryRecord(
            id=delivery_id,
            endpoint_id=endpoint.id,
            workspace_id=workspace_id,
            event_id=event_id,
            event_type=event_type,
            status="rejected",
            attempts=0,
            public_payload=self._public_event(event),
            reason=reason,
        )
        self._delivery_records[delivery_id] = record
        return record

    def _public_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        return shape_public_payload(event)

    def _require_endpoint(self, endpoint_id: str) -> WebhookEndpoint:
        endpoint = self._endpoints.get(endpoint_id)
        if not endpoint:
            raise WebhookDeliveryError(
                f"unknown webhook endpoint: {endpoint_id}",
            )
        return endpoint

    @staticmethod
    def _delivery_id(endpoint_id: str, event_id: str) -> str:
        return f"{endpoint_id}:{event_id}"
