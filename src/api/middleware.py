"""API middleware components."""

import base64
import json
import logging
import os
import time
from typing import Any, Callable, Dict, Iterable, Mapping, Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class AuthError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class WorkerTokenValidator:
    def __init__(
        self,
        now: Callable[[], float] = time.time,
        required_scope: Optional[str] = None,
        required_role: Optional[str] = None,
        not_before: Optional[float] = None,
        revoked_tokens: Optional[Iterable[str]] = None,
        revoked_jtis: Optional[Iterable[str]] = None,
    ):
        self.now = now
        if required_scope is None:
            required_scope = os.getenv("WORKER_AUTH_REQUIRED_SCOPE", "worker")
        if required_role is None:
            required_role = os.getenv("WORKER_AUTH_REQUIRED_ROLE", "worker")
        if not_before is None:
            not_before = _optional_timestamp(
                os.getenv("WORKER_AUTH_NOT_BEFORE")
                or os.getenv("AO_WORKER_AUTH_NOT_BEFORE")
            )

        self.required_scope = required_scope
        self.required_role = required_role
        self.not_before = not_before
        self.revoked_tokens = set(
            revoked_tokens or _env_list("WORKER_AUTH_REVOKED_TOKENS")
        )
        self.revoked_jtis = set(
            revoked_jtis or _env_list("WORKER_AUTH_REVOKED_JTIS")
        )

    def validate(self, token: str, request: Request) -> Dict[str, Any]:
        if token in self.revoked_tokens:
            raise AuthError(401, "Token revoked")

        claims = _decode_claims(token)
        jti = claims.get("jti")
        if jti and str(jti) in self.revoked_jtis:
            raise AuthError(401, "Token revoked")

        now = self.now()
        nbf = _optional_timestamp(claims.get("nbf"))
        if nbf is not None and nbf > now:
            raise AuthError(401, "Token not active")

        exp = _optional_timestamp(claims.get("exp"))
        if exp is not None and exp <= now:
            raise AuthError(401, "Token expired")

        iat = _optional_timestamp(claims.get("iat"))
        if (
            self.not_before is not None
            and (iat is None or iat < self.not_before)
        ):
            raise AuthError(401, "Token is stale")

        subject = str(claims.get("sub", "")).strip()
        anonymous_subjects = {"anonymous", "guest", "unauthenticated"}
        if not subject or subject.lower() in anonymous_subjects:
            raise AuthError(401, "Anonymous principals are not allowed")

        if self.required_scope and not _has_scope(claims, self.required_scope):
            raise AuthError(403, "Insufficient token scope")

        workspace_id = request.headers.get("X-Workspace-ID") or os.getenv(
            "WORKER_AUTH_WORKSPACE_ID"
        )
        token_workspace = claims.get("workspace_id") or claims.get("workspace")
        if workspace_id and token_workspace != workspace_id:
            raise AuthError(403, "Workspace access denied")

        if self.required_role and not _has_role(
            claims, self.required_role, workspace_id
        ):
            raise AuthError(403, "Workspace role required")

        return claims


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, validator: Optional[WorkerTokenValidator] = None):
        super().__init__(app)
        self.validator = validator or WorkerTokenValidator()

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        if (
            request.url.path.startswith("/api/v2")
            and request.url.path != "/api/v2/auth/token"
        ):
            token = _extract_worker_token(request)
            if not token:
                return Response(status_code=401, content="Unauthorized")
            try:
                request.state.worker_claims = self.validator.validate(
                    token, request
                )
            except AuthError as exc:
                return Response(
                    status_code=exc.status_code, content=exc.message
                )
        return await call_next(request)


def _extract_worker_token(request: Request) -> Optional[str]:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.removeprefix("Bearer ").strip()
    for cookie_name in ("ao_worker_token", "worker_token"):
        token = request.cookies.get(cookie_name)
        if token:
            return token.strip()
    return None


def _decode_claims(token: str) -> Dict[str, Any]:
    token = token.strip()
    if not token:
        raise AuthError(401, "Invalid token")

    parts = token.split(".")
    payload = parts[1] if len(parts) >= 2 else token
    try:
        padding = "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode((payload + padding).encode("ascii"))
        claims = json.loads(decoded.decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError):
        raise AuthError(401, "Invalid token")

    if not isinstance(claims, dict):
        raise AuthError(401, "Invalid token")
    return claims


def _optional_timestamp(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise AuthError(401, "Invalid token time")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise AuthError(401, "Invalid token time")


def _env_list(name: str) -> Iterable[str]:
    raw = os.getenv(name, "")
    return [
        item.strip()
        for item in raw.replace("\n", ",").split(",")
        if item.strip()
    ]


def _claim_values(value: Any) -> Iterable[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [
            part.strip()
            for part in value.replace(",", " ").split()
            if part.strip()
        ]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return [str(value).strip()]


def _has_scope(claims: Mapping[str, Any], required_scope: str) -> bool:
    scopes = set(_claim_values(claims.get("scope")))
    scopes.update(_claim_values(claims.get("scopes")))
    return (
        "*" in scopes
        or required_scope in scopes
        or any(scope.startswith(f"{required_scope}:") for scope in scopes)
    )


def _has_role(
    claims: Mapping[str, Any],
    required_role: str,
    workspace_id: Optional[str],
) -> bool:
    roles = claims.get("roles", claims.get("role"))
    if isinstance(roles, Mapping):
        workspace_roles = (
            _claim_values(roles.get(workspace_id)) if workspace_id else []
        )
        global_roles = _claim_values(roles.get("*")) + _claim_values(
            roles.get("global")
        )
        role_values = set(workspace_roles + global_roles)
    else:
        role_values = set(_claim_values(roles))
    return "admin" in role_values or required_role in role_values


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 100, window: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window
        self._requests = {}

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        if client_ip not in self._requests:
            self._requests[client_ip] = []

        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if now - t < self.window
        ]

        if len(self._requests[client_ip]) >= self.max_requests:
            return Response(status_code=429, content="Too many requests")

        self._requests[client_ip].append(now)
        return await call_next(request)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start
        logger.info(
            "%s %s %s %.3fs",
            request.method,
            request.url.path,
            response.status_code,
            duration,
        )
        return response

# 2019-03-01T18:35:19 update

# 2019-04-03T13:22:05 update

# 2019-04-30T17:18:49 update

# 2019-08-20T09:29:03 update

# 2019-08-30T15:52:06 update

# 2019-11-23T16:58:42 update

# 2020-02-18T10:04:07 update

# 2020-04-21T17:35:30 update

# 2020-05-22T11:10:34 update

# 2020-07-02T12:31:26 update

# 2020-07-05T13:52:59 update

# 2020-08-21T20:36:45 update

# 2021-01-19T09:17:15 update

# 2021-01-29T11:34:24 update

# 2021-02-04T15:21:21 update

# 2021-04-19T19:23:15 update

# 2021-05-20T16:50:15 update

# 2021-06-22T19:23:44 update

# 2021-09-09T13:44:55 update

# 2021-09-16T09:30:20 update

# 2021-10-14T20:42:33 update

# 2021-12-28T16:39:14 update

# 2022-01-26T19:07:27 update

# 2022-01-28T08:03:41 update

# 2022-03-23T12:17:02 update

# 2022-04-06T12:12:27 update

# 2022-04-21T14:53:01 update

# 2022-06-30T08:37:32 update

# 2022-07-06T10:44:45 update

# 2022-11-02T11:12:47 update

# 2022-11-15T20:54:21 update

# 2022-11-23T14:13:34 update

# 2023-01-26T10:03:44 update

# 2023-02-09T17:08:10 update

# 2023-02-16T10:04:00 update

# 2023-03-14T11:52:03 update

# 2023-04-10T12:42:07 update

# 2023-04-26T10:43:39 update

# 2023-06-27T08:18:07 update

# 2023-08-30T15:30:40 update

# 2023-08-30T14:10:05 update

# 2023-10-09T18:32:46 update

# 2023-11-21T20:35:55 update

# 2024-03-07T19:17:39 update

# 2024-04-01T18:06:19 update

# 2024-07-18T15:37:34 update

# 2024-07-25T09:21:53 update

# 2024-08-12T14:24:22 update

# 2024-11-18T08:50:54 update

# 2025-04-08T12:43:05 update

# 2025-06-03T08:10:47 update

# 2025-06-12T08:37:52 update

# 2025-06-17T08:36:56 update

# 2025-07-02T18:09:42 update

# 2025-07-22T12:39:21 update

# 2025-10-13T12:13:46 update

# 2025-12-05T09:44:22 update

# 2025-12-22T18:34:47 update

# 2026-01-26T15:36:23 update

# 2026-02-13T12:36:40 update

# 2026-02-26T11:07:15 update

# 2026-03-19T11:00:17 update

# 2026-03-27T12:58:53 update

# 2026-05-12T17:19:36 update
