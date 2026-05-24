import base64
import json

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.api.middleware import AuthMiddleware, WorkerTokenValidator


NOW = 1_800_000_000


def _token(claims):
    payload = json.dumps(claims, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _client(**validator_kwargs):
    app = FastAPI()
    validator = WorkerTokenValidator(now=lambda: NOW, **validator_kwargs)
    app.add_middleware(AuthMiddleware, validator=validator)

    @app.get("/api/v2/worker")
    async def worker_route(request: Request):
        return {"subject": request.state.worker_claims["sub"]}

    return TestClient(app)


def _claims(**overrides):
    claims = {
        "sub": "worker-1",
        "scope": "worker:dispatch",
        "roles": {"workspace-a": ["worker"]},
        "workspace_id": "workspace-a",
        "iat": NOW,
        "nbf": NOW - 1,
        "exp": NOW + 60,
        "jti": "token-1",
    }
    claims.update(overrides)
    return claims


def _get(client, claims, **kwargs):
    return client.get(
        "/api/v2/worker",
        headers={
            "Authorization": f"Bearer {_token(claims)}",
            "X-Workspace-ID": "workspace-a",
        },
        **kwargs,
    )


def test_worker_auth_rejects_token_before_not_before_time():
    response = _get(_client(), _claims(nbf=NOW + 30))

    assert response.status_code == 401
    assert response.text == "Token not active"


def test_worker_auth_rejects_stale_token_before_global_cutoff():
    response = _get(_client(not_before=NOW), _claims(iat=NOW - 1))

    assert response.status_code == 401
    assert response.text == "Token is stale"


def test_worker_auth_rejects_revoked_token_id():
    response = _get(_client(revoked_jtis={"token-1"}), _claims())

    assert response.status_code == 401
    assert response.text == "Token revoked"


def test_worker_auth_rejects_malformed_token():
    response = _client().get(
        "/api/v2/worker",
        headers={
            "Authorization": "Bearer not-json",
            "X-Workspace-ID": "workspace-a",
        },
    )

    assert response.status_code == 401
    assert response.text == "Invalid token"


def test_worker_auth_rejects_anonymous_principal():
    response = _get(_client(), _claims(sub="anonymous"))

    assert response.status_code == 401
    assert response.text == "Anonymous principals are not allowed"


def test_worker_auth_rejects_insufficient_scope():
    response = _get(_client(), _claims(scope="profile:read"))

    assert response.status_code == 403
    assert response.text == "Insufficient token scope"


def test_worker_auth_rejects_missing_workspace_role():
    response = _get(_client(), _claims(roles={"workspace-a": ["viewer"]}))

    assert response.status_code == 403
    assert response.text == "Workspace role required"


def test_worker_auth_accepts_authorized_workspace_worker():
    response = _get(_client(), _claims())

    assert response.status_code == 200
    assert response.json() == {"subject": "worker-1"}


def test_worker_auth_applies_same_validation_to_browser_cookie():
    token = _token(_claims())
    client = _client()
    client.cookies.set("ao_worker_token", token)
    response = client.get(
        "/api/v2/worker",
        headers={"X-Workspace-ID": "workspace-a"},
    )

    assert response.status_code == 200
    assert response.json() == {"subject": "worker-1"}
