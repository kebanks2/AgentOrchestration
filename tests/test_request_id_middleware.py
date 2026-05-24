import logging

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.testclient import TestClient

from src.api.middleware import (
    AuthMiddleware,
    LoggingMiddleware,
    RequestIDLogFilter,
    RequestIDMiddleware,
    get_request_id,
)


def _app(include_auth=False):
    app = FastAPI()

    @app.get("/ok")
    async def ok(background_tasks: BackgroundTasks, request: Request):
        request_id = get_request_id()

        def log_background_task():
            logging.getLogger("tests.background").info("background finished")

        background_tasks.add_task(log_background_task)
        return {
            "request_id": request_id,
            "state_request_id": request.scope["headers"] is not None,
        }

    @app.get("/api/v2/secure")
    async def secure():
        return {"ok": True}

    @app.get("/boom")
    async def boom():
        logging.getLogger("tests.exception").info("about to raise")
        raise RuntimeError("do not expose this")

    if include_auth:
        app.add_middleware(AuthMiddleware)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RequestIDMiddleware)
    return app


def _install_caplog_filter(caplog):
    caplog.handler.addFilter(RequestIDLogFilter())


def test_request_id_header_reused_for_request_and_background_logs(caplog):
    _install_caplog_filter(caplog)
    caplog.set_level(logging.INFO)

    response = TestClient(_app()).get(
        "/ok",
        headers={"X-Request-ID": "req-123"},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-123"
    assert response.json()["request_id"] == "req-123"
    background_records = [
        record
        for record in caplog.records
        if record.name == "tests.background"
    ]
    assert background_records
    assert {record.request_id for record in background_records} == {"req-123"}
    assert get_request_id() == "-"


def test_rejected_request_gets_sanitized_request_id_header_and_log(caplog):
    _install_caplog_filter(caplog)
    caplog.set_level(logging.INFO)
    raw_request_id = "bad id with secret"

    response = TestClient(_app(include_auth=True)).get(
        "/api/v2/secure",
        headers={"X-Request-ID": raw_request_id},
    )

    assert response.status_code == 401
    assert response.headers["X-Request-ID"] != raw_request_id
    assert " " not in response.headers["X-Request-ID"]
    request_logs = [
        record
        for record in caplog.records
        if record.name == "src.api.middleware"
    ]
    assert request_logs
    assert raw_request_id not in caplog.text
    assert {record.request_id for record in request_logs} == {
        response.headers["X-Request-ID"]
    }
    assert get_request_id() == "-"


def test_exception_path_logs_request_id_and_clears_context(caplog):
    _install_caplog_filter(caplog)
    caplog.set_level(logging.INFO)

    response = TestClient(
        _app(),
        raise_server_exceptions=False,
    ).get("/boom", headers={"X-Request-ID": "req-error"})

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "req-error"
    assert "do not expose this" not in response.text
    exception_records = [
        record
        for record in caplog.records
        if record.name in {"tests.exception", "src.api.middleware"}
    ]
    assert exception_records
    assert {record.request_id for record in exception_records} == {
        "req-error"
    }
    assert get_request_id() == "-"
