"""Central authorization checks for automation API requests."""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from src.common.metrics import metrics


class AuthError(Exception):
    """Raised when a request cannot be authenticated or authorized."""

    def __init__(self, status_code: int, reason: str):
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason


@dataclass(frozen=True)
class AutomationPrincipal:
    subject: str
    token_type: str
    scopes: Set[str] = field(default_factory=set)
    workspace_roles: Set[str] = field(default_factory=set)
    expires_at: Optional[float] = None
    revoked: bool = False


@dataclass(frozen=True)
class AuthorizationPolicy:
    machine_scopes: Set[str] = field(default_factory=set)
    user_roles: Set[str] = field(default_factory=set)


READ_POLICY = AuthorizationPolicy(
    machine_scopes={"automation:read"},
    user_roles={"viewer", "operator", "admin"},
)
WRITE_POLICY = AuthorizationPolicy(
    machine_scopes={"automation:write"},
    user_roles={"operator", "admin"},
)


class AutomationAuthService:
    """Validates automation tokens before route handling can run."""

    def __init__(
        self,
        tokens: Optional[Dict[str, AutomationPrincipal]] = None,
        now: Optional[Callable[[], float]] = None,
    ):
        self._tokens = (
            tokens if tokens is not None else self._load_tokens_from_env()
        )
        self._now = now or time.time
        self.audit_events: List[Dict[str, Any]] = []

    def authorize_header(
        self,
        authorization: str,
        policy: AuthorizationPolicy,
        client_kind: Optional[str] = None,
    ) -> AutomationPrincipal:
        token = self._extract_bearer_token(authorization)
        principal = self._tokens.get(token)
        if principal is None:
            self._record("denied", "unknown_token")
            raise AuthError(401, "unknown_token")

        if principal.revoked:
            self._record("denied", "revoked_token", principal)
            raise AuthError(401, "revoked_token")

        if (
            principal.expires_at is not None
            and principal.expires_at <= self._now()
        ):
            self._record("denied", "stale_token", principal)
            raise AuthError(401, "stale_token")

        expected_kind = client_kind or principal.token_type
        if expected_kind == "machine":
            self._require_token_type(principal, "machine")
            self._require_any(
                principal.scopes,
                policy.machine_scopes,
                "missing_machine_scope",
                principal,
            )
        elif expected_kind == "browser":
            self._require_token_type(principal, "user")
            self._require_any(
                principal.workspace_roles,
                policy.user_roles,
                "missing_workspace_role",
                principal,
            )
        else:
            self._record("denied", "unsupported_client_kind", principal)
            raise AuthError(403, "unsupported_client_kind")

        self._record("allowed", "authorized", principal)
        return principal

    def _extract_bearer_token(self, authorization: str) -> str:
        if not authorization:
            self._record("denied", "anonymous")
            raise AuthError(401, "anonymous")

        parts = authorization.split()
        if (
            len(parts) != 2
            or parts[0].lower() != "bearer"
            or not parts[1].strip()
        ):
            self._record("denied", "malformed_authorization")
            raise AuthError(401, "malformed_authorization")

        return parts[1].strip()

    def _require_token_type(
        self,
        principal: AutomationPrincipal,
        token_type: str,
    ) -> None:
        if principal.token_type != token_type:
            self._record(
                "denied",
                f"{principal.token_type}_token_for_{token_type}_client",
                principal,
            )
            raise AuthError(403, "wrong_token_type")

    def _require_any(
        self,
        actual_values: Iterable[str],
        required_values: Iterable[str],
        reason: str,
        principal: AutomationPrincipal,
    ) -> None:
        required = set(required_values)
        if not required.intersection(actual_values):
            self._record("denied", reason, principal)
            raise AuthError(403, reason)

    def _record(
        self,
        decision: str,
        reason: str,
        principal: Optional[AutomationPrincipal] = None,
    ) -> None:
        metrics.increment(f"auth.{decision}")
        self.audit_events.append(
            {
                "decision": decision,
                "reason": reason,
                "principal": principal.subject if principal else None,
                "token_type": principal.token_type if principal else None,
            }
        )

    def _load_tokens_from_env(self) -> Dict[str, AutomationPrincipal]:
        raw_tokens = os.getenv("AO_AUTOMATION_TOKENS", "")
        if not raw_tokens:
            return {}

        data = json.loads(raw_tokens)
        tokens = {}
        for token, config in data.items():
            tokens[token] = AutomationPrincipal(
                subject=config["subject"],
                token_type=config["token_type"],
                scopes=set(config.get("scopes", [])),
                workspace_roles=set(config.get("workspace_roles", [])),
                expires_at=config.get("expires_at"),
                revoked=bool(config.get("revoked", False)),
            )
        return tokens
