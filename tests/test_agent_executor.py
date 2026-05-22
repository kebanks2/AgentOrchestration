import asyncio

import pytest

from src.agent.executor import AgentExecutor


@pytest.mark.parametrize("max_concurrent", [0, -1, -5, 1.5, "2", True])
def test_rejects_invalid_max_concurrent_values(max_concurrent):
    with pytest.raises(
        ValueError,
        match="max_concurrent must be a positive integer",
    ):
        AgentExecutor(max_concurrent=max_concurrent)


def test_accepts_positive_integer_max_concurrent():
    executor = AgentExecutor(max_concurrent=2)

    assert executor.max_concurrent == 2


def test_valid_executor_still_runs_task():
    async def handler(agent_id, task):
        return {"agent_id": agent_id, "task_id": task["id"]}

    async def run():
        executor = AgentExecutor(max_concurrent=1)
        execution_id = await executor.execute(
            "agent-1",
            {"id": "task-1"},
            handler,
        )
        return executor.get_result(execution_id)

    result = asyncio.run(run())

    assert result["agent_id"] == "agent-1"
    assert result["task_id"] == "task-1"
    assert result["result"] == {
        "agent_id": "agent-1",
        "task_id": "task-1",
    }
