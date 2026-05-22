"""Authentication and protected-route authorization helpers."""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Set


PUBLIC_PATHS = {"/health", "/api/v2/auth/token"}
PROTECTED_PREFIXES = ("/api/v2", "/api/docs", "/api/redoc", "/openapi.json")
ROLE_RANKS = {"viewer": 1, "reader": 1, "editor": 2, "admin": 3}


@dataclass(frozen=True)
class AuthPrincipal:
    subject: str
    token: str
    scopes: Set[str] = field(default_factory=set)
    workspace_roles: Dict[str, str] = field(default_factory=dict)
    expires_at: Optional[float] = None
    revoked: bool = False


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    reason: str
    status_code: int = 401
    principal: Optional[AuthPrincipal] = None


class AuthService:
    def __init__(
        self,
        principals: Optional[Mapping[str, AuthPrincipal]] = None,
    ):
        self._principals = dict(principals or {})

    @classmethod
    def from_config(cls, config: Optional[Mapping] = None) -> "AuthService":
        config = config or {}
        principals: Dict[str, AuthPrincipal] = {}

        for token, entry in config.get("tokens", {}).items():
            principals[token] = cls._principal_from_entry(token, entry)

        env_tokens = os.getenv("AO_AUTH_TOKENS")
        if env_tokens:
            for token, entry in json.loads(env_tokens).items():
                principals[token] = cls._principal_from_entry(token, entry)

        env_token = os.getenv("AO_AUTH_TOKEN")
        if env_token and env_token not in principals:
            principals[env_token] = AuthPrincipal(
                subject="environment-token",
                token=env_token,
                scopes={"orchestration:read", "orchestration:write"},
                workspace_roles={"default": "admin"},
            )

        return cls(principals)

    @staticmethod
    def _principal_from_entry(token: str, entry: Mapping) -> AuthPrincipal:
        return AuthPrincipal(
            subject=str(entry.get("subject", "unknown")),
            token=token,
            scopes=set(entry.get("scopes", [])),
            workspace_roles=dict(entry.get("workspace_roles", {})),
            expires_at=entry.get("expires_at"),
            revoked=bool(entry.get("revoked", False)),
        )

    def authenticate(
        self,
        authorization: str,
        session_cookie: Optional[str] = None,
        now: Optional[float] = None,
    ) -> AuthorizationDecision:
        token = self._extract_token(authorization, session_cookie)
        if token is None:
            return AuthorizationDecision(False, "anonymous", 401)
        if token == "":
            return AuthorizationDecision(False, "malformed", 401)

        principal = self._principals.get(token)
        if principal is None:
            return AuthorizationDecision(False, "unknown", 401)
        if principal.revoked:
            return AuthorizationDecision(False, "revoked", 401)
        current_time = now or time.time()
        if (
            principal.expires_at is not None
            and principal.expires_at <= current_time
        ):
            return AuthorizationDecision(False, "stale", 401)

        return AuthorizationDecision(
            True,
            "authenticated",
            principal=principal,
        )

    def authorize(
        self,
        authorization: str,
        *,
        session_cookie: Optional[str] = None,
        method: str = "GET",
        workspace_id: str = "default",
        now: Optional[float] = None,
    ) -> AuthorizationDecision:
        decision = self.authenticate(authorization, session_cookie, now)
        if not decision.allowed:
            return decision

        principal = decision.principal
        assert principal is not None
        required_scope = self.required_scope(method)
        if required_scope not in principal.scopes:
            return AuthorizationDecision(
                False,
                "insufficient_scope",
                403,
                principal,
            )

        required_role = self.required_role(method)
        actual_role = principal.workspace_roles.get(workspace_id)
        if ROLE_RANKS.get(actual_role, 0) < ROLE_RANKS[required_role]:
            return AuthorizationDecision(
                False,
                "insufficient_role",
                403,
                principal,
            )

        return AuthorizationDecision(True, "authorized", principal=principal)

    @staticmethod
    def required_scope(method: str) -> str:
        if method.upper() in {"GET", "HEAD", "OPTIONS"}:
            return "orchestration:read"
        return "orchestration:write"

    @staticmethod
    def required_role(method: str) -> str:
        if method.upper() in {"GET", "HEAD", "OPTIONS"}:
            return "viewer"
        return "editor"

    @staticmethod
    def _extract_token(
        authorization: str,
        session_cookie: Optional[str],
    ) -> Optional[str]:
        if authorization:
            if not authorization.startswith("Bearer "):
                return ""
            return authorization[len("Bearer "):].strip()
        if session_cookie is not None:
            return session_cookie.strip()
        return None


def canonical_path(path: str) -> str:
    collapsed = "/" + "/".join(part for part in path.split("/") if part)
    if path.endswith("/") and collapsed != "/":
        return collapsed
    return collapsed


def is_protected_path(path: str) -> bool:
    normalized = canonical_path(path)
    if normalized in PUBLIC_PATHS:
        return False
    return any(
        normalized == prefix or normalized.startswith(f"{prefix}/")
        for prefix in PROTECTED_PREFIXES
    )
