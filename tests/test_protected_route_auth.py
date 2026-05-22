import time

from fastapi.testclient import TestClient

from src.api.auth import AuthPrincipal
from src.api.server import create_app


def make_client(tokens):
    token_config = {
        token: {
            "subject": principal.subject,
            "scopes": sorted(principal.scopes),
            "workspace_roles": principal.workspace_roles,
            "expires_at": principal.expires_at,
            "revoked": principal.revoked,
        }
        for token, principal in tokens.items()
    }
    return TestClient(create_app({"auth": {"tokens": token_config}}))


def principal(
    token,
    *,
    scopes=None,
    role="viewer",
    expires_at=None,
    revoked=False,
):
    resolved_scopes = {"orchestration:read"} if scopes is None else set(scopes)
    return AuthPrincipal(
        subject=f"user-{token}",
        token=token,
        scopes=resolved_scopes,
        workspace_roles={"default": role},
        expires_at=expires_at,
        revoked=revoked,
    )


def test_trailing_slash_redirect_candidate_requires_auth_before_redirect():
    client = make_client({})

    response = client.get("/api/v2/agents/", follow_redirects=False)
    repeated_slash_response = client.get(
        "/api/v2//agents",
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert repeated_slash_response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_anonymous_and_malformed_principals_are_denied():
    client = make_client({"valid": principal("valid")})

    assert client.get("/api/v2/agents").status_code == 401
    token_response = client.get(
        "/api/v2/agents",
        headers={"Authorization": "Token valid"},
    )
    blank_response = client.get(
        "/api/v2/agents",
        headers={"Authorization": "Bearer   "},
    )

    assert token_response.status_code == 401
    assert blank_response.status_code == 401


def test_revoked_stale_and_insufficient_principals_are_denied():
    client = make_client(
        {
            "revoked": principal("revoked", revoked=True),
            "stale": principal("stale", expires_at=time.time() - 1),
            "no-scope": principal("no-scope", scopes=set()),
            "no-role": principal("no-role", role=""),
        }
    )

    revoked = client.get(
        "/api/v2/agents",
        headers={"Authorization": "Bearer revoked"},
    )
    stale = client.get(
        "/api/v2/agents",
        headers={"Authorization": "Bearer stale"},
    )
    no_scope = client.get(
        "/api/v2/agents",
        headers={"Authorization": "Bearer no-scope"},
    )
    no_role = client.get(
        "/api/v2/agents",
        headers={"Authorization": "Bearer no-role"},
    )

    assert revoked.status_code == 401
    assert stale.status_code == 401
    assert no_scope.status_code == 403
    assert no_role.status_code == 403


def test_browser_session_cookie_uses_same_protected_route_guard():
    client = make_client({"session-token": principal("session-token")})
    client.cookies.set("ao_session", "session-token")

    response = client.get("/api/v2/agents")

    assert response.status_code == 200
    assert response.json() == {"agents": []}


def test_revoked_browser_session_cookie_is_denied_before_handler():
    client = make_client(
        {"session-token": principal("session-token", revoked=True)}
    )
    client.cookies.set("ao_session", "session-token")

    response = client.get("/api/v2/agents/")

    assert response.status_code == 401


def test_wrong_workspace_role_is_denied_before_handler():
    client = make_client({"valid": principal("valid", role="editor")})

    response = client.post(
        "/api/v2/agents/",
        params={"name": "worker-a", "agent_type": "worker.processor"},
        headers={
            "Authorization": "Bearer valid",
            "X-Workspace-ID": "other-workspace",
        },
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_authorized_workspace_role_still_completes_read_and_write_workflow():
    client = make_client(
        {
            "writer": principal(
                "writer",
                scopes={"orchestration:read", "orchestration:write"},
                role="editor",
            )
        }
    )
    headers = {"Authorization": "Bearer writer"}

    create_response = client.post(
        "/api/v2/agents",
        params={"name": "worker-a", "agent_type": "worker.processor"},
        headers=headers,
    )
    assert create_response.status_code == 200

    list_response = client.get("/api/v2/agents/", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()["agents"][0]["name"] == "worker-a"
