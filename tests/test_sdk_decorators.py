import asyncio
import time

import pytest

from src.sdk.decorators import task


def test_task_decorator_runs_sync_handler():
    @task(name="sync-handler", retries=2, timeout=1)
    def handler(value):
        return value + 1

    assert asyncio.run(handler(1)) == 2
    assert handler.__task_config__ == {
        "name": "sync-handler",
        "retries": 2,
        "timeout": 1,
    }


def test_task_decorator_runs_async_handler():
    @task(name="async-handler", timeout=1)
    async def handler(value):
        await asyncio.sleep(0)
        return value + 1

    assert asyncio.run(handler(1)) == 2
    assert handler.__task_config__["name"] == "async-handler"


def test_task_decorator_times_out_sync_handler():
    @task(name="slow-sync", timeout=0.01)
    def handler():
        time.sleep(0.05)
        return "late"

    with pytest.raises(TimeoutError, match="slow-sync"):
        asyncio.run(handler())
