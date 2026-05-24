import asyncio

from src.orchestrator.engine import OrchestrationEngine
from src.orchestrator.event_bus import RunEventBus


def test_event_bus_rejects_duplicate_and_out_of_order_events():
    async def scenario():
        bus = RunEventBus()
        accepted = await bus.publish("run-1", 1, "task_started")
        duplicate = await bus.publish("run-1", 1, "task_started")
        running = await bus.publish("run-1", 2, "task_running")
        stale = await bus.publish("run-1", 1, "task_stale", producer="worker")
        return bus, accepted, duplicate, running, stale

    bus, accepted, duplicate, running, stale = asyncio.run(scenario())

    assert accepted.accepted
    assert duplicate.accepted is False
    assert duplicate.reason == "duplicate_event"
    assert running.accepted
    assert stale.accepted is False
    assert stale.reason == "out_of_order"
    assert [event.event_type for event in bus.events("run-1")] == [
        "task_started",
        "task_running",
    ]


def test_event_bus_records_one_terminal_outcome_under_concurrency():
    async def scenario():
        bus = RunEventBus()
        await bus.publish("run-2", 1, "task_started", producer="scheduler")
        results = await asyncio.gather(
            bus.publish(
                "run-2",
                2,
                "task_completed",
                producer="worker-a",
                terminal=True,
            ),
            bus.publish(
                "run-2",
                3,
                "task_failed",
                producer="worker-b",
                terminal=True,
            ),
        )
        return bus, results

    bus, results = asyncio.run(scenario())

    accepted = [result for result in results if result.accepted]
    rejected = [result for result in results if not result.accepted]
    terminal_events = [
        event for event in bus.events("run-2") if event.terminal
    ]

    assert len(accepted) == 1
    assert len(rejected) == 1
    assert rejected[0].reason == "terminal_outcome_recorded"
    assert terminal_events == [accepted[0].record]
    assert bus.terminal_outcome("run-2") == accepted[0].record


def test_engine_persists_terminal_outcome_before_post_execute_hooks():
    async def scenario():
        engine = OrchestrationEngine()
        agent_id = engine.registry.register("worker", "worker.processor")
        task = {"id": "task-1", "target_agent": agent_id}

        async def fail_after_terminal_event(_task, _result):
            raise RuntimeError("side effect failed")

        engine.register_hook("post_execute", fail_after_terminal_event)
        await engine._execute_task(task)
        return engine.event_bus

    bus = asyncio.run(scenario())
    events = bus.events("task-1")
    terminal_events = [event for event in events if event.terminal]

    assert [event.event_type for event in events] == [
        "task_started",
        "task_completed",
    ]
    assert len(terminal_events) == 1
    assert bus.terminal_outcome("task-1").event_type == "task_completed"


def test_engine_records_terminal_failure_when_pre_execute_hook_fails():
    async def scenario():
        engine = OrchestrationEngine()
        agent_id = engine.registry.register("worker", "worker.processor")
        task = {"id": "task-2", "target_agent": agent_id}

        async def fail_before_agent_execution(_task):
            raise RuntimeError("preflight failed")

        engine.register_hook("pre_execute", fail_before_agent_execution)
        await engine._execute_task(task)
        return engine.event_bus, agent_id

    bus, agent_id = asyncio.run(scenario())
    events = bus.events("task-2")

    assert [event.event_type for event in events] == [
        "task_started",
        "task_failed",
    ]
    assert bus.terminal_outcome("task-2").payload == {
        "agent_id": agent_id,
        "error": "RuntimeError",
    }
