from fastapi.testclient import TestClient

from src.api.auth import IntegrationAuthService, Principal
from src.api.server import create_app


def make_client():
    auth_service = IntegrationAuthService(now=lambda: 1000)
    auth_service.register_token(
        "valid-admin-token",
        Principal(
            subject="active-admin",
            scopes={"webhooks:manage"},
            workspace_roles={"acme": "admin"},
            expires_at=2000,
        ),
    )
    auth_service.register_token(
        "browser-session-token",
        Principal(
            subject="browser-admin",
            scopes={"webhooks:manage"},
            workspace_roles={"acme": "owner"},
            expires_at=2000,
        ),
    )
    auth_service.register_token(
        "expired-token",
        Principal(
            subject="stale-admin",
            scopes={"webhooks:manage"},
            workspace_roles={"acme": "admin"},
            expires_at=900,
        ),
    )
    auth_service.register_token(
        "revoked-token",
        Principal(
            subject="revoked-admin",
            scopes={"webhooks:manage"},
            workspace_roles={"acme": "admin"},
            revoked=True,
            expires_at=2000,
        ),
    )
    auth_service.register_token(
        "disabled-token",
        Principal(
            subject="disabled-admin",
            scopes={"webhooks:manage"},
            workspace_roles={"acme": "admin"},
            disabled=True,
            expires_at=2000,
        ),
    )
    auth_service.register_token(
        "anonymous-token",
        Principal(
            subject="anonymous",
            scopes={"webhooks:manage"},
            workspace_roles={"acme": "admin"},
            anonymous=True,
            expires_at=2000,
        ),
    )
    auth_service.register_token(
        "missing-scope-token",
        Principal(
            subject="viewer",
            scopes={"agents:read"},
            workspace_roles={"acme": "admin"},
            expires_at=2000,
        ),
    )
    auth_service.register_token(
        "wrong-role-token",
        Principal(
            subject="workspace-viewer",
            scopes={"webhooks:manage"},
            workspace_roles={"acme": "viewer"},
            expires_at=2000,
        ),
    )
    return TestClient(create_app({"auth_service": auth_service}))


def create_webhook(client, token=None, session_token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if session_token:
        client.cookies.set("ao_session", session_token)
    return client.post(
        "/api/v2/workspaces/acme/webhooks",
        params={
            "target_url": "https://example.com/hook",
            "event": "agent.started",
        },
        headers=headers,
    )


def test_webhook_management_rejects_anonymous_client():
    response = create_webhook(make_client())
    assert response.status_code == 401


def test_webhook_management_rejects_expired_credentials():
    response = create_webhook(make_client(), token="expired-token")
    assert response.status_code == 401


def test_webhook_management_rejects_revoked_credentials():
    response = create_webhook(make_client(), token="revoked-token")
    assert response.status_code == 401


def test_webhook_management_rejects_anonymous_credentials():
    response = create_webhook(make_client(), token="anonymous-token")
    assert response.status_code == 401


def test_webhook_management_rejects_disabled_principal():
    response = create_webhook(make_client(), token="disabled-token")
    assert response.status_code == 403


def test_webhook_management_rejects_insufficient_scope():
    response = create_webhook(make_client(), token="missing-scope-token")
    assert response.status_code == 403


def test_webhook_management_rejects_insufficient_workspace_role():
    response = create_webhook(make_client(), token="wrong-role-token")
    assert response.status_code == 403


def test_webhook_management_allows_authorized_token_client():
    response = create_webhook(make_client(), token="valid-admin-token")
    assert response.status_code == 200
    assert response.json()["status"] == "created"


def test_webhook_management_allows_authorized_browser_session():
    response = create_webhook(
        make_client(),
        session_token="browser-session-token",
    )
    assert response.status_code == 200
    assert response.json()["status"] == "created"
