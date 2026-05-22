from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.api.middleware import UploadBoundaryMiddleware


def _client(endpoint):
    app = Starlette(routes=[Route("/upload", endpoint, methods=["POST"])])
    app.add_middleware(UploadBoundaryMiddleware)
    return TestClient(app, raise_server_exceptions=False)


async def _ok_upload(request):
    assert request.state.upload_boundary_validated is True
    await request.body()
    return PlainTextResponse("ok")


def test_upload_boundary_allows_valid_multipart_request():
    client = _client(_ok_upload)

    response = client.post(
        "/upload",
        headers={"Content-Type": "multipart/form-data; boundary=abc123"},
        content=b"--abc123\r\n\r\n--abc123--\r\n",
    )

    assert response.status_code == 200
    assert response.headers["X-Upload-Boundary-Validated"] == "true"


def test_upload_boundary_rejects_missing_boundary_before_handler(caplog):
    handler_called = False
    secret = "customer-token-should-not-appear"

    async def upload(request):
        nonlocal handler_called
        handler_called = True
        await request.body()
        return PlainTextResponse("unreachable")

    client = _client(upload)

    with caplog.at_level("WARNING", logger="src.api.middleware"):
        response = client.post(
            "/upload",
            headers={"Content-Type": "multipart/form-data"},
            content=secret.encode(),
        )

    assert response.status_code == 400
    assert response.headers["X-Upload-Rejection"] == "multipart-boundary"
    assert handler_called is False
    assert secret not in caplog.text
    assert "multipart/form-data" not in caplog.text


def test_upload_boundary_rejects_invalid_boundary_before_handler():
    handler_called = False

    async def upload(request):
        nonlocal handler_called
        handler_called = True
        await request.body()
        return PlainTextResponse("unreachable")

    client = _client(upload)

    response = client.post(
        "/upload",
        headers={"Content-Type": "multipart/form-data; boundary=bad boundary"},
        content=b"not-read",
    )

    assert response.status_code == 400
    assert handler_called is False


def test_upload_boundary_clears_state_after_success():
    seen_state = []

    async def upload(request):
        seen_state.append(request.state)
        assert request.state.upload_boundary_validated is True
        return PlainTextResponse("ok")

    client = _client(upload)

    response = client.post(
        "/upload",
        headers={"Content-Type": "multipart/form-data; boundary=abc123"},
        content=b"--abc123\r\n\r\n--abc123--\r\n",
    )

    assert response.status_code == 200
    assert len(seen_state) == 1
    assert not hasattr(seen_state[0], "upload_boundary_validated")


def test_upload_boundary_clears_state_after_exception():
    seen_state = []

    async def upload(request):
        seen_state.append(request.state)
        assert request.state.upload_boundary_validated is True
        raise RuntimeError("boom")

    client = _client(upload)

    response = client.post(
        "/upload",
        headers={"Content-Type": "multipart/form-data; boundary=abc123"},
        content=b"--abc123\r\n\r\n--abc123--\r\n",
    )

    assert response.status_code == 500
    assert len(seen_state) == 1
    assert not hasattr(seen_state[0], "upload_boundary_validated")


def test_upload_boundary_ignores_non_multipart_requests():
    async def upload(request):
        assert not hasattr(request.state, "upload_boundary_validated")
        return PlainTextResponse("ok")

    client = _client(upload)

    response = client.post(
        "/upload",
        headers={"Content-Type": "application/json"},
        content=b"{}",
    )

    assert response.status_code == 200
    assert "X-Upload-Boundary-Validated" not in response.headers
