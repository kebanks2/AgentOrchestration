from fastapi.testclient import TestClient

from src.agent import AgentRegistry
from src.api import routes
from src.api.server import create_app


class CountingRegistry(AgentRegistry):
    def __init__(self):
        super().__init__()
        self.calls = {
            "list": 0,
            "register": 0,
            "get": 0,
            "delete": 0,
            "update_status": 0,
            "count": 0,
        }

    def list(self, *args, **kwargs):
        self.calls["list"] += 1
        return super().list(*args, **kwargs)

    def register(self, *args, **kwargs):
        self.calls["register"] += 1
        return super().register(*args, **kwargs)

    def get(self, *args, **kwargs):
        self.calls["get"] += 1
        return super().get(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.calls["delete"] += 1
        return super().delete(*args, **kwargs)

    def update_status(self, *args, **kwargs):
        self.calls["update_status"] += 1
        return super().update_status(*args, **kwargs)

    def count(self):
        self.calls["count"] += 1
        return super().count()


def make_client():
    registry = CountingRegistry()
    routes.registry = registry
    return TestClient(create_app()), registry


def auth_headers():
    return {"Authorization": "Bearer test-token"}


def test_unauthorized_request_stops_before_registry_lookup():
    client, registry = make_client()

    response = client.get("/api/v2/agents")

    assert response.status_code == 401
    assert registry.calls["list"] == 0


def test_malformed_status_returns_consistent_400_before_lookup():
    client, registry = make_client()

    response = client.get(
        "/api/v2/agents?status=definitely-invalid",
        headers=auth_headers(),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "validation_error"
    assert "status must be one of" in response.json()["detail"]["message"]
    assert registry.calls["list"] == 0


def test_malformed_agent_id_returns_400_before_mutation():
    client, registry = make_client()

    response = client.post(
        "/api/v2/agents/not-a-uuid/start",
        headers=auth_headers(),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "validation_error",
        "message": "agent_id must be a valid UUID",
    }
    assert registry.calls["update_status"] == 0


def test_blank_registration_field_returns_400_before_mutation():
    client, registry = make_client()

    response = client.post(
        "/api/v2/agents?name=&agent_type=worker.http",
        headers=auth_headers(),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "validation_error",
        "message": "name must be a non-empty string",
    }
    assert registry.calls["register"] == 0


def test_authorized_valid_request_still_completes_workflow():
    client, registry = make_client()

    register_response = client.post(
        "/api/v2/agents?name=worker&agent_type=worker.http",
        headers=auth_headers(),
    )
    agent_id = register_response.json()["agent_id"]
    start_response = client.post(
        f"/api/v2/agents/{agent_id}/start",
        headers=auth_headers(),
    )
    count_response = client.get("/api/v2/agents/count", headers=auth_headers())

    assert register_response.status_code == 200
    assert start_response.status_code == 200
    assert count_response.status_code == 200
    assert count_response.json() == {"count": 1}
    assert registry.calls["register"] == 1
    assert registry.calls["update_status"] == 1
    assert registry.calls["count"] == 1
