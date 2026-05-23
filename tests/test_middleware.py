import asyncio

import pytest
from starlette.requests import Request
from starlette.responses import Response

from src.api.middleware import (
    CancellationPropagationMiddleware,
    register_downstream_task,
    request_cancelled,
)


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def _request(headers=None):
    raw_headers = [
        (key.lower().encode("ascii"), value.encode("ascii"))
        for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v2/agents",
            "headers": raw_headers,
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        },
        _receive,
    )


def _middleware():
    return CancellationPropagationMiddleware(
        app=lambda scope, receive, send: None,
    )


async def _sleep_forever():
    await asyncio.Event().wait()


def test_normal_request_completes_and_clears_request_state():
    request = _request()

    async def call_next(received):
        assert request_cancelled(received) is False
        task = register_downstream_task(
            received,
            asyncio.create_task(asyncio.sleep(0)),
        )
        await task
        return Response(status_code=204)

    response = asyncio.run(_middleware().dispatch(request, call_next))

    assert response.status_code == 204
    assert response.headers["X-Agent-Request-State"] == "completed"
    assert not hasattr(request.state, "downstream_agent_tasks")
    assert not hasattr(request.state, "agent_request_cancelled")


def test_cancelled_header_rejects_before_downstream_dispatch():
    request = _request({"x-agent-request-cancelled": "true"})
    called = False

    async def call_next(received):
        nonlocal called
        called = True
        return Response(status_code=200)

    response = asyncio.run(_middleware().dispatch(request, call_next))

    assert called is False
    assert response.status_code == 499
    assert response.headers["X-Agent-Request-State"] == "cancelled"
    assert response.headers["X-Agent-Cancellation"] == "pre-dispatch"
    assert not hasattr(request.state, "downstream_agent_tasks")


def test_downstream_cancellation_cancels_registered_agent_task():
    request = _request()
    agent_task = None

    async def call_next(received):
        nonlocal agent_task
        agent_task = register_downstream_task(
            received,
            asyncio.create_task(_sleep_forever()),
        )
        raise asyncio.CancelledError()

    response = asyncio.run(_middleware().dispatch(request, call_next))

    assert response.status_code == 499
    assert response.headers["X-Agent-Request-State"] == "cancelled"
    assert response.headers["X-Agent-Cancellation"] == "propagated"
    assert agent_task.cancelled()
    assert not hasattr(request.state, "downstream_agent_tasks")


def test_exception_path_cancels_agent_task_and_clears_state():
    request = _request()
    agent_task = None

    async def call_next(received):
        nonlocal agent_task
        agent_task = register_downstream_task(
            received,
            asyncio.create_task(_sleep_forever()),
        )
        raise RuntimeError("handler failed")

    with pytest.raises(RuntimeError):
        asyncio.run(_middleware().dispatch(request, call_next))

    assert agent_task.cancelled()
    assert not hasattr(request.state, "downstream_agent_tasks")
    assert not hasattr(request.state, "agent_request_cancelled")
