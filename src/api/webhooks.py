"""Webhook ingress verification and replay protection."""

from dataclasses import dataclass
import hashlib
import hmac
import time
from typing import Dict, Mapping, Optional, Tuple


class WebhookVerificationError(ValueError):
    """Raised when a webhook cannot be trusted for processing."""


@dataclass(frozen=True)
class WebhookSubscription:
    workspace_id: str
    endpoint_id: str
    secret: str
    enabled: bool = True
    replay_window_seconds: int = 300


@dataclass(frozen=True)
class WebhookDeliveryRecord:
    workspace_id: str
    endpoint_id: str
    delivery_id: str
    accepted_at: int
    idempotency_key: str
    duplicate: bool = False


class WebhookReplayStore:
    def __init__(self):
        self._signatures: Dict[Tuple[str, str, str], int] = {}
        self._deliveries: Dict[
            Tuple[str, str, str],
            Tuple[str, WebhookDeliveryRecord],
        ] = {}

    def record(
        self,
        subscription: WebhookSubscription,
        delivery_id: str,
        signature: str,
        timestamp: int,
        now: Optional[int] = None,
    ) -> WebhookDeliveryRecord:
        now = int(time.time() if now is None else now)
        self._prune(now, subscription.replay_window_seconds)

        delivery_key = (
            subscription.workspace_id,
            subscription.endpoint_id,
            delivery_id,
        )
        existing_delivery = self._deliveries.get(delivery_key)
        if existing_delivery:
            existing_signature, record = existing_delivery
            if existing_signature != signature:
                raise WebhookVerificationError(
                    "Webhook delivery id was reused with a different signature"
                )
            return WebhookDeliveryRecord(
                workspace_id=record.workspace_id,
                endpoint_id=record.endpoint_id,
                delivery_id=record.delivery_id,
                accepted_at=record.accepted_at,
                idempotency_key=record.idempotency_key,
                duplicate=True,
            )

        signature_key = (
            subscription.workspace_id,
            subscription.endpoint_id,
            signature,
        )
        if signature_key in self._signatures:
            raise WebhookVerificationError(
                "Webhook signature has already been used"
            )

        record = WebhookDeliveryRecord(
            workspace_id=subscription.workspace_id,
            endpoint_id=subscription.endpoint_id,
            delivery_id=delivery_id,
            accepted_at=now,
            idempotency_key=_idempotency_key(
                subscription,
                delivery_id,
                signature,
            ),
        )
        self._signatures[signature_key] = now
        self._deliveries[delivery_key] = (signature, record)
        return record

    def _prune(self, now: int, replay_window_seconds: int) -> None:
        cutoff = now - replay_window_seconds
        self._signatures = {
            key: seen_at
            for key, seen_at in self._signatures.items()
            if seen_at >= cutoff
        }
        self._deliveries = {
            key: value
            for key, value in self._deliveries.items()
            if value[1].accepted_at >= cutoff
        }


class WebhookVerifier:
    def __init__(self, replay_store: Optional[WebhookReplayStore] = None):
        self._replay_store = replay_store or WebhookReplayStore()

    def verify(
        self,
        subscription: WebhookSubscription,
        body: bytes,
        headers: Mapping[str, str],
        now: Optional[int] = None,
    ) -> WebhookDeliveryRecord:
        now = int(time.time() if now is None else now)
        if not subscription.enabled:
            raise WebhookVerificationError("Webhook endpoint is disabled")
        if not subscription.secret:
            raise WebhookVerificationError("Webhook endpoint has no secret")

        delivery_id = _required_header(headers, "X-AO-Delivery-ID")
        timestamp = _parse_timestamp(
            _required_header(headers, "X-AO-Timestamp")
        )
        if abs(now - timestamp) > subscription.replay_window_seconds:
            raise WebhookVerificationError(
                "Webhook timestamp is outside the replay window"
            )

        signature = _required_header(headers, "X-AO-Signature")
        expected = sign_webhook_payload(subscription.secret, timestamp, body)
        if not hmac.compare_digest(signature, expected):
            raise WebhookVerificationError("Webhook signature is invalid")

        return self._replay_store.record(
            subscription,
            delivery_id,
            signature,
            timestamp,
            now=now,
        )


def sign_webhook_payload(secret: str, timestamp: int, body: bytes) -> str:
    message = str(timestamp).encode("ascii") + b"." + body
    digest = hmac.new(
        secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def _required_header(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name) or headers.get(name.lower())
    if not isinstance(value, str) or not value.strip():
        raise WebhookVerificationError(f"Missing webhook header {name}")
    return value.strip()


def _parse_timestamp(value: str) -> int:
    try:
        timestamp = int(value)
    except ValueError as exc:
        raise WebhookVerificationError(
            "Webhook timestamp must be a unix timestamp"
        ) from exc
    if timestamp <= 0:
        raise WebhookVerificationError(
            "Webhook timestamp must be a unix timestamp"
        )
    return timestamp


def _idempotency_key(
    subscription: WebhookSubscription,
    delivery_id: str,
    signature: str,
) -> str:
    parts = [
        subscription.workspace_id,
        subscription.endpoint_id,
        delivery_id,
        signature,
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
