"""Integration authentication helpers."""

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, Optional, Set

from starlette.requests import Request


class AuthError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class Principal:
    subject: str
    scopes: Set[str] = field(default_factory=set)
    workspace_roles: Dict[str, str] = field(default_factory=dict)
    anonymous: bool = False
    disabled: bool = False
    revoked: bool = False
    expires_at: Optional[float] = None

    def is_expired(self, now: float) -> bool:
        return self.expires_at is not None and self.expires_at <= now


class IntegrationAuthService:
    def __init__(self, now: Callable[[], float] = time.time):
        self._tokens: Dict[str, Principal] = {}
        self._now = now

    def register_token(self, token: str, principal: Principal) -> None:
        self._tokens[token] = principal

    def authenticate(self, token: str) -> Principal:
        principal = self._tokens.get(token)
        if principal is None:
            raise AuthError(401, "Invalid credentials")
        if principal.revoked:
            raise AuthError(401, "Revoked credentials")
        if principal.anonymous:
            raise AuthError(401, "Anonymous credentials")
        if principal.is_expired(self._now()):
            raise AuthError(401, "Expired credentials")
        if principal.disabled:
            raise AuthError(403, "Disabled principal")
        return principal


def extract_integration_token(request: Request) -> Optional[str]:
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return request.cookies.get("ao_session")


def require_scope(principal: Principal, required_scope: str) -> None:
    if required_scope not in principal.scopes:
        raise AuthError(403, "Insufficient scope")


def require_workspace_role(
    principal: Principal,
    workspace_id: str,
    allowed_roles: Iterable[str],
) -> None:
    if principal.workspace_roles.get(workspace_id) not in set(allowed_roles):
        raise AuthError(403, "Insufficient workspace role")
