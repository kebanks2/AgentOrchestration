from fastapi.testclient import TestClient

from src.api.auth import IntegrationAuthService, Principal
from src.api.routes import RunCancellationService
from src.api.server import create_app


def make_client():
    auth_service = IntegrationAuthService(now=lambda: 1000)
    auth_service.register_token(
        "valid-operator-token",
        Principal(
            subject="active-operator",
            scopes={"runs:cancel"},
            workspace_roles={"acme": "operator"},
            expires_at=2000,
        ),
    )
    auth_service.register_token(
        "browser-session-token",
        Principal(
            subject="browser-admin",
            scopes={"runs:cancel"},
            workspace_roles={"acme": "admin"},
            expires_at=2000,
        ),
    )
    auth_service.register_token(
        "expired-token",
        Principal(
            subject="stale-operator",
            scopes={"runs:cancel"},
            workspace_roles={"acme": "operator"},
            expires_at=900,
        ),
    )
    auth_service.register_token(
        "revoked-token",
        Principal(
            subject="revoked-operator",
            scopes={"runs:cancel"},
            workspace_roles={"acme": "operator"},
            revoked=True,
            expires_at=2000,
        ),
    )
    auth_service.register_token(
        "anonymous-token",
        Principal(
            subject="anonymous",
            scopes={"runs:cancel"},
            workspace_roles={"acme": "operator"},
            anonymous=True,
            expires_at=2000,
        ),
    )
    auth_service.register_token(
        "disabled-token",
        Principal(
            subject="disabled-operator",
            scopes={"runs:cancel"},
            workspace_roles={"acme": "operator"},
            disabled=True,
            expires_at=2000,
        ),
    )
    auth_service.register_token(
        "missing-scope-token",
        Principal(
            subject="run-viewer",
            scopes={"runs:read"},
            workspace_roles={"acme": "operator"},
            expires_at=2000,
        ),
    )
    auth_service.register_token(
        "wrong-role-token",
        Principal(
            subject="workspace-viewer",
            scopes={"runs:cancel"},
            workspace_roles={"acme": "viewer"},
            expires_at=2000,
        ),
    )
    auth_service.register_token(
        "other-workspace-token",
        Principal(
            subject="other-operator",
            scopes={"runs:cancel"},
            workspace_roles={"other": "operator"},
            expires_at=2000,
        ),
    )

    cancellation_service = RunCancellationService()
    cancellation_service.register_run("run-123", "acme")
    app = create_app(
        {
            "auth_service": auth_service,
            "run_cancellation_service": cancellation_service,
        }
    )
    return TestClient(app), cancellation_service


def cancel_run(client, token=None, session_token=None, run_id="run-123"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if session_token:
        client.cookies.set("ao_session", session_token)
    return client.post(
        f"/api/v2/workspaces/acme/runs/{run_id}/cancel",
        headers=headers,
    )


def test_run_cancellation_rejects_missing_credentials():
    client, _ = make_client()
    response = cancel_run(client)
    assert response.status_code == 401


def test_run_cancellation_rejects_unknown_credentials():
    client, _ = make_client()
    response = cancel_run(client, token="unknown-token")
    assert response.status_code == 401


def test_run_cancellation_rejects_stale_credentials():
    client, _ = make_client()
    response = cancel_run(client, token="expired-token")
    assert response.status_code == 401


def test_run_cancellation_rejects_revoked_credentials():
    client, _ = make_client()
    response = cancel_run(client, token="revoked-token")
    assert response.status_code == 401


def test_run_cancellation_rejects_anonymous_credentials():
    client, _ = make_client()
    response = cancel_run(client, token="anonymous-token")
    assert response.status_code == 401


def test_run_cancellation_rejects_disabled_operator():
    client, _ = make_client()
    response = cancel_run(client, token="disabled-token")
    assert response.status_code == 403


def test_run_cancellation_rejects_insufficient_scope():
    client, _ = make_client()
    response = cancel_run(client, token="missing-scope-token")
    assert response.status_code == 403


def test_run_cancellation_rejects_insufficient_workspace_role():
    client, _ = make_client()
    response = cancel_run(client, token="wrong-role-token")
    assert response.status_code == 403


def test_run_cancellation_rejects_other_workspace_role():
    client, _ = make_client()
    response = cancel_run(client, token="other-workspace-token")
    assert response.status_code == 403


def test_run_cancellation_authorizes_before_run_lookup():
    client, cancellation_service = make_client()
    response = cancel_run(
        client,
        token="wrong-role-token",
        run_id="missing-run",
    )
    assert response.status_code == 403
    assert cancellation_service.cancelled_runs == []


def test_run_cancellation_reports_missing_run_after_authorization():
    client, cancellation_service = make_client()
    response = cancel_run(
        client,
        token="valid-operator-token",
        run_id="missing-run",
    )
    assert response.status_code == 404
    assert cancellation_service.cancelled_runs == []


def test_run_cancellation_allows_authorized_operator_token():
    client, cancellation_service = make_client()
    response = cancel_run(client, token="valid-operator-token")
    assert response.status_code == 200
    assert response.json() == {
        "status": "cancelled",
        "workspace_id": "acme",
        "run_id": "run-123",
    }
    assert cancellation_service.cancelled_runs == [("acme", "run-123")]


def test_run_cancellation_allows_authorized_browser_session():
    client, cancellation_service = make_client()
    response = cancel_run(client, session_token="browser-session-token")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert cancellation_service.cancelled_runs == [("acme", "run-123")]
