"""Webhook delivery audit helpers."""

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


INTERNAL_FIELD_NAMES = {
    "authorization",
    "debug",
    "headers",
    "internal_run_id",
    "private",
    "secret",
    "token",
    "trace_id",
}


class DeliveryRejected(ValueError):
    """Raised when a webhook delivery cannot be recorded safely."""


@dataclass(frozen=True)
class WebhookEndpoint:
    workspace_id: str
    endpoint_id: str
    url: str
    enabled: bool = True


@dataclass(frozen=True)
class DeliveryRecord:
    workspace_id: str
    endpoint_id: str
    delivery_id: str
    status: str
    sequence: int
    attempt: int
    delivered_at: float
    payload: Dict[str, Any]

    def public_dict(self) -> Dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "endpoint_id": self.endpoint_id,
            "delivery_id": self.delivery_id,
            "status": self.status,
            "sequence": self.sequence,
            "attempt": self.attempt,
            "delivered_at": self.delivered_at,
            "payload": self.payload,
        }


class DeliveryAuditStore:
    """In-memory delivery audit store with endpoint-scoped writes."""

    VALID_STATUSES = {"pending", "delivered", "failed", "retrying"}

    def __init__(self):
        self._endpoints: Dict[Tuple[str, str], WebhookEndpoint] = {}
        self._records: Dict[Tuple[str, str, str], DeliveryRecord] = {}

    def register_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
        url: str,
        enabled: bool = True,
    ) -> WebhookEndpoint:
        endpoint = WebhookEndpoint(
            workspace_id=_clean_identifier(workspace_id, "workspace_id"),
            endpoint_id=_clean_identifier(endpoint_id, "endpoint_id"),
            url=_clean_url(url),
            enabled=bool(enabled),
        )
        endpoint_key = (endpoint.workspace_id, endpoint.endpoint_id)
        self._endpoints[endpoint_key] = endpoint
        return endpoint

    def disable_endpoint(self, workspace_id: str, endpoint_id: str) -> bool:
        key = (
            _clean_identifier(workspace_id, "workspace_id"),
            _clean_identifier(endpoint_id, "endpoint_id"),
        )
        endpoint = self._endpoints.get(key)
        if not endpoint:
            return False
        self._endpoints[key] = WebhookEndpoint(
            workspace_id=endpoint.workspace_id,
            endpoint_id=endpoint.endpoint_id,
            url=endpoint.url,
            enabled=False,
        )
        return True

    def record_delivery(
        self,
        workspace_id: str,
        endpoint_id: str,
        delivery_id: str,
        status: str,
        payload: Dict[str, Any],
        sequence: int,
        attempt: int = 1,
        delivered_at: Optional[float] = None,
    ) -> DeliveryRecord:
        workspace_id = _clean_identifier(workspace_id, "workspace_id")
        endpoint_id = _clean_identifier(endpoint_id, "endpoint_id")
        delivery_id = _clean_identifier(delivery_id, "delivery_id")
        status = _clean_status(status, self.VALID_STATUSES)
        sequence = _clean_non_negative_int(sequence, "sequence")
        attempt = _clean_positive_int(attempt, "attempt")

        endpoint = self._endpoints.get((workspace_id, endpoint_id))
        if endpoint is None or not endpoint.enabled:
            raise DeliveryRejected("webhook endpoint is not enabled")
        if not isinstance(payload, dict):
            raise DeliveryRejected("webhook payload must be an object")

        record = DeliveryRecord(
            workspace_id=workspace_id,
            endpoint_id=endpoint_id,
            delivery_id=delivery_id,
            status=status,
            sequence=sequence,
            attempt=attempt,
            delivered_at=time.time() if delivered_at is None else delivered_at,
            payload=_sanitize_payload(payload),
        )

        key = (workspace_id, endpoint_id, delivery_id)
        previous = self._records.get(key)
        if previous and _record_order(record) < _record_order(previous):
            return previous

        self._records[key] = record
        return record

    def get_delivery(
        self,
        workspace_id: str,
        endpoint_id: str,
        delivery_id: str,
    ) -> Optional[Dict[str, Any]]:
        key = (
            _clean_identifier(workspace_id, "workspace_id"),
            _clean_identifier(endpoint_id, "endpoint_id"),
            _clean_identifier(delivery_id, "delivery_id"),
        )
        record = self._records.get(key)
        return record.public_dict() if record else None

    def get_last_status(
        self,
        workspace_id: str,
        endpoint_id: str,
    ) -> Optional[Dict[str, Any]]:
        workspace_id = _clean_identifier(workspace_id, "workspace_id")
        endpoint_id = _clean_identifier(endpoint_id, "endpoint_id")
        matches = [
            record
            for key, record in self._records.items()
            if key[0] == workspace_id and key[1] == endpoint_id
        ]
        if not matches:
            return None
        return max(matches, key=_record_order).public_dict()


def _record_order(record: DeliveryRecord) -> Tuple[int, int, float]:
    return (record.sequence, record.attempt, record.delivered_at)


def _clean_identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise DeliveryRejected(f"{field_name} must be a string")
    value = value.strip()
    if not value:
        raise DeliveryRejected(f"{field_name} is required")
    return value


def _clean_url(value: str) -> str:
    value = _clean_identifier(value, "url")
    if not value.startswith(("https://", "http://")):
        raise DeliveryRejected("webhook endpoint url must be absolute")
    return value


def _clean_status(value: str, valid_statuses) -> str:
    value = _clean_identifier(value, "status").lower()
    if value not in valid_statuses:
        raise DeliveryRejected("invalid webhook delivery status")
    return value


def _clean_non_negative_int(value: int, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DeliveryRejected(f"{field_name} must be a non-negative integer")
    return value


def _clean_positive_int(value: int, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise DeliveryRejected(f"{field_name} must be a positive integer")
    return value


def _sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_payload(item)
            for key, item in value.items()
            if not _is_internal_field(key)
        }
    if isinstance(value, list):
        return [_sanitize_payload(item) for item in value]
    return value


def _is_internal_field(key: Any) -> bool:
    if not isinstance(key, str):
        return True
    normalized = key.strip().lower()
    return (
        normalized.startswith("_")
        or normalized in INTERNAL_FIELD_NAMES
        or "token" in normalized
        or "secret" in normalized
    )
