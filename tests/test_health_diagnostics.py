import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api import server


def test_public_health_excludes_execution_metadata():
    client = TestClient(server.create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "version": server.VERSION,
    }


def test_route_authorized_diagnostics_are_redacted():
    client = TestClient(server.create_app())

    response = client.get(
        "/health?include_execution_metadata=true",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "version": server.VERSION,
        "diagnostics": {},
    }


def test_authorized_diagnostics_are_redacted_before_serialization():
    called = {"value": False}

    def metadata_provider():
        called["value"] = True
        return {
            "worker": "ready",
            "process_id": 123,
            "nested": {
                "task_payload": {"secret": "raw"},
                "safe": "kept",
            },
            "workers": [
                {"id": "worker-1", "token": "hidden"},
                {"id": "worker-2", "status": "idle"},
            ],
        }

    response = server.build_public_health_response(
        include_execution_metadata="true",
        authorization="Bearer test-token",
        metadata_provider=metadata_provider,
    )

    assert called["value"] is True
    assert response == {
        "status": "healthy",
        "version": server.VERSION,
        "diagnostics": {
            "worker": "ready",
            "nested": {"safe": "kept"},
            "workers": [
                {"id": "worker-1"},
                {"id": "worker-2", "status": "idle"},
            ],
        },
    }


def test_unauthorized_diagnostics_request_returns_401_without_lookup():
    def metadata_provider():
        raise AssertionError("metadata lookup should not run")

    with pytest.raises(HTTPException) as exc_info:
        server.build_public_health_response(
            include_execution_metadata="true",
            authorization=None,
            metadata_provider=metadata_provider,
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "execution metadata requires authorization"


def test_malformed_diagnostics_flag_returns_400_without_lookup():
    def metadata_provider():
        raise AssertionError("metadata lookup should not run")

    with pytest.raises(HTTPException) as exc_info:
        server.build_public_health_response(
            include_execution_metadata="sometimes",
            authorization="Bearer test-token",
            metadata_provider=metadata_provider,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == (
        "include_execution_metadata must be true or false"
    )


def test_route_returns_deterministic_4xx_for_unauthorized_and_malformed():
    client = TestClient(server.create_app())

    unauthorized = client.get("/health?include_execution_metadata=true")
    malformed = client.get(
        "/health?include_execution_metadata=sometimes",
        headers={"Authorization": "Bearer test-token"},
    )

    assert unauthorized.status_code == 401
    assert unauthorized.json() == {
        "detail": "execution metadata requires authorization"
    }
    assert malformed.status_code == 400
    assert malformed.json() == {
        "detail": "include_execution_metadata must be true or false"
    }
