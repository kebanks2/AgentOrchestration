import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from src.api.middleware import CacheControlMiddleware
from src.api.server import create_app


AUTH_HEADERS = {"Authorization": "Bearer cache-test-token"}


def test_authenticated_json_response_is_not_cacheable(caplog):
    client = TestClient(create_app())

    with caplog.at_level(logging.INFO, logger="src.api.middleware"):
        response = client.get("/api/v2/agents", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"
    assert "Authorization" in response.headers["vary"]
    assert "Cookie" in response.headers["vary"]
    assert "cache-control applied" in caplog.text
    assert "cache-test-token" not in caplog.text
    assert "Authorization" not in caplog.text


def test_public_json_response_is_not_marked_authenticated_cache_control():
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert "cache-control" not in response.headers
    assert "pragma" not in response.headers


def test_rejected_request_does_not_emit_authenticated_cache_headers(caplog):
    client = TestClient(create_app())

    with caplog.at_level(logging.INFO, logger="src.api.middleware"):
        response = client.get("/api/v2/agents")

    assert response.status_code == 401
    assert "cache-control" not in response.headers
    assert "cache-control applied" not in caplog.text


def test_existing_vary_values_are_preserved_for_authenticated_json():
    app = FastAPI()
    app.add_middleware(CacheControlMiddleware)

    @app.get("/json")
    async def json_response():
        return JSONResponse(
            {"ok": True},
            headers={"Vary": "Accept-Encoding"},
        )

    client = TestClient(app)

    response = client.get("/json", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["vary"] == "Accept-Encoding, Authorization, Cookie"


def test_exception_path_does_not_leak_authenticated_cache_state():
    app = FastAPI()
    app.add_middleware(CacheControlMiddleware)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("network token secret should not be logged")

    @app.get("/json")
    async def json_response():
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)

    error_response = client.get("/boom", headers=AUTH_HEADERS)
    next_response = client.get("/json")

    assert error_response.status_code == 500
    assert "cache-control" not in next_response.headers
