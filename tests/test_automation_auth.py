import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import AutomationAuthService, AutomationPrincipal
from src.api.middleware import AuthMiddleware


def build_client(now=100):
    tokens = {
        "machine-read": AutomationPrincipal(
            "worker-read",
            "machine",
            scopes={"automation:read"},
        ),
        "machine-write": AutomationPrincipal(
            "worker-write",
            "machine",
            scopes={"automation:write"},
        ),
        "user-viewer": AutomationPrincipal(
            "user-viewer",
            "user",
            workspace_roles={"viewer"},
        ),
        "user-operator": AutomationPrincipal(
            "user-operator",
            "user",
            workspace_roles={"operator"},
        ),
        "machine-revoked": AutomationPrincipal(
            "worker-revoked",
            "machine",
            scopes={"automation:write"},
            revoked=True,
        ),
        "machine-stale": AutomationPrincipal(
            "worker-stale",
            "machine",
            scopes={"automation:write"},
            expires_at=99,
        ),
        "machine-unscoped": AutomationPrincipal(
            "worker-unscoped",
            "machine",
            scopes={"automation:read"},
        ),
        "user-unprivileged": AutomationPrincipal(
            "user-unprivileged",
            "user",
            workspace_roles={"viewer"},
        ),
    }
    auth_service = AutomationAuthService(tokens=tokens, now=lambda: now)
    app = FastAPI()
    app.add_middleware(AuthMiddleware, auth_service=auth_service)

    @app.get("/api/v2/agents")
    async def list_agents():
        return {"ok": True}

    @app.post("/api/v2/agents/start")
    async def start_agent():
        return {"ok": True}

    @app.post("/api/v2/auth/token")
    async def issue_token():
        return {"token": "issued"}

    return TestClient(app), auth_service


def test_anonymous_automation_request_fails_closed():
    client, auth_service = build_client()

    response = client.get("/api/v2/agents")

    assert response.status_code == 401
    assert response.text == "anonymous"
    assert auth_service.audit_events[-1]["reason"] == "anonymous"


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        ("unknown", "unknown_token"),
        ("machine-revoked", "revoked_token"),
        ("machine-stale", "stale_token"),
    ],
)
def test_stale_revoked_and_unknown_tokens_are_denied(token, reason):
    client, auth_service = build_client()

    response = client.post(
        "/api/v2/agents/start",
        headers={
            "Authorization": f"Bearer {token}",
            "X-AO-Client-Kind": "machine",
        },
    )

    assert response.status_code == 401
    assert response.text == reason
    assert auth_service.audit_events[-1]["reason"] == reason


def test_machine_token_requires_write_scope_for_mutation_routes():
    client, auth_service = build_client()

    response = client.post(
        "/api/v2/agents/start",
        headers={
            "Authorization": "Bearer machine-unscoped",
            "X-AO-Client-Kind": "machine",
        },
    )

    assert response.status_code == 403
    assert response.text == "missing_machine_scope"
    assert auth_service.audit_events[-1] == {
        "decision": "denied",
        "reason": "missing_machine_scope",
        "principal": "worker-unscoped",
        "token_type": "machine",
    }


def test_user_token_requires_operator_role_for_mutation_routes():
    client, auth_service = build_client()

    response = client.post(
        "/api/v2/agents/start",
        headers={
            "Authorization": "Bearer user-unprivileged",
            "X-AO-Client-Kind": "browser",
        },
    )

    assert response.status_code == 403
    assert response.text == "missing_workspace_role"
    assert auth_service.audit_events[-1]["principal"] == "user-unprivileged"


def test_machine_and_user_tokens_are_not_interchangeable():
    client, auth_service = build_client()

    machine_as_browser = client.post(
        "/api/v2/agents/start",
        headers={
            "Authorization": "Bearer machine-write",
            "X-AO-Client-Kind": "browser",
        },
    )
    user_as_machine = client.post(
        "/api/v2/agents/start",
        headers={
            "Authorization": "Bearer user-operator",
            "X-AO-Client-Kind": "machine",
        },
    )

    assert machine_as_browser.status_code == 403
    assert user_as_machine.status_code == 403
    assert [event["reason"] for event in auth_service.audit_events[-2:]] == [
        "machine_token_for_user_client",
        "user_token_for_machine_client",
    ]


def test_authorized_machine_and_user_principals_complete_workflows():
    client, auth_service = build_client()

    read_response = client.get(
        "/api/v2/agents",
        headers={
            "Authorization": "Bearer machine-read",
            "X-AO-Client-Kind": "machine",
        },
    )
    write_response = client.post(
        "/api/v2/agents/start",
        headers={
            "Authorization": "Bearer user-operator",
            "X-AO-Client-Kind": "browser",
        },
    )

    assert read_response.status_code == 200
    assert write_response.status_code == 200
    assert auth_service.audit_events[-2]["reason"] == "authorized"
    assert auth_service.audit_events[-1]["reason"] == "authorized"


def test_auth_token_endpoint_stays_public():
    client, auth_service = build_client()

    response = client.post("/api/v2/auth/token")

    assert response.status_code == 200
    assert response.json() == {"token": "issued"}
    assert auth_service.audit_events == []
