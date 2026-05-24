import asyncio
import json

from src.agent.executor import AgentExecutor


def test_executor_records_json_serializable_success_result():
    executor = AgentExecutor()

    async def handler(agent_id, task):
        return {"agent_id": agent_id, "values": [task["id"], "ok"]}

    execution_id = asyncio.run(
        executor.execute("agent-1", {"id": "task-1"}, handler)
    )

    result = executor.get_result(execution_id)
    assert result["status"] == "completed"
    assert result["result"] == {
        "agent_id": "agent-1",
        "values": ["task-1", "ok"],
    }
    assert not executor._active_tasks
    json.dumps(result)


def test_executor_fails_closed_for_non_json_serializable_result():
    executor = AgentExecutor()

    async def handler(agent_id, task):
        return {"ids": {task["id"]}}

    execution_id = asyncio.run(
        executor.execute("agent-1", {"id": "task-2"}, handler)
    )

    result = executor.get_result(execution_id)
    assert result["status"] == "failed"
    assert result["error_type"] == "ResultSerializationError"
    assert "JSON serializable" in result["error"]
    assert result["task_id"] == "task-2"
    assert not executor._active_tasks
    json.dumps(result)


def test_executor_records_cancelled_terminal_outcome_once():
    async def scenario():
        executor = AgentExecutor()

        async def handler(agent_id, task):
            await asyncio.Event().wait()

        run = asyncio.create_task(
            executor.execute("agent-1", {"id": "task-3"}, handler)
        )

        for _ in range(50):
            if executor._active_tasks:
                break
            await asyncio.sleep(0)

        assert len(executor._active_tasks) == 1
        execution_id = next(iter(executor._active_tasks))
        assert executor.cancel(execution_id)

        returned_id = await asyncio.wait_for(run, timeout=1)
        result = executor.get_result(execution_id)

        assert returned_id == execution_id
        assert list(executor._results) == [execution_id]
        assert result["status"] == "cancelled"
        assert result["task_id"] == "task-3"
        assert not executor._active_tasks
        json.dumps(result)

    asyncio.run(scenario())
