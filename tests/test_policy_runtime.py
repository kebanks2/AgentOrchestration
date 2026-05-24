import asyncio

from src.agent.registry import AgentStatus
from src.common.policy import PolicyDecision, PolicyRuntime
from src.orchestrator.engine import OrchestrationEngine


def _dequeue(scheduler):
    return asyncio.run(scheduler.dequeue())


def _run(coro):
    return asyncio.run(coro)


def test_policy_unavailable_fails_closed_before_side_effects(monkeypatch):
    engine = OrchestrationEngine(policy_runtime=PolicyRuntime(available=False))
    agent_id = engine.registry.register("policy-agent", "worker.processor")
    task_id = engine.scheduler.enqueue(
        {"target_agent": agent_id, "type": "policy"}
    )
    task = _dequeue(engine.scheduler)
    hook_state = {"terminal_seen": False, "calls": 0}

    async def should_not_run(agent, task):
        raise AssertionError("policy-denied task reached worker dispatch")

    async def on_error(task, error):
        hook_state["calls"] += 1
        hook_state["terminal_seen"] = (
            engine.scheduler.terminal_outcome(task["id"]) is not None
        )

    monkeypatch.setattr(engine, "_run_agent_task", should_not_run)
    engine.register_hook("pre_execute", should_not_run)
    engine.register_hook("on_error", on_error)

    _run(engine._execute_task(task))

    outcome = engine.scheduler.terminal_outcome(task_id)
    assert outcome["status"] == "failed"
    assert outcome["reason"] == "policy_unavailable"
    assert task_id not in engine.scheduler._in_flight
    assert hook_state == {"terminal_seen": True, "calls": 1}
    assert engine.registry.get(agent_id)["status"] == AgentStatus.PENDING.value


def test_terminal_policy_failure_is_idempotent(monkeypatch):
    engine = OrchestrationEngine(policy_runtime=PolicyRuntime(available=False))
    agent_id = engine.registry.register("policy-agent", "worker.processor")
    task_id = engine.scheduler.enqueue(
        {"target_agent": agent_id, "type": "policy"}
    )
    task = _dequeue(engine.scheduler)
    errors = []

    async def on_error(task, error):
        errors.append(str(error))

    async def should_not_run(agent, task):
        raise AssertionError("terminal task was dispatched")

    engine.register_hook("on_error", on_error)
    monkeypatch.setattr(engine, "_run_agent_task", should_not_run)

    _run(engine._execute_task(task))
    engine.policy_runtime.set_available(True)
    _run(engine._execute_task(task))

    assert len(errors) == 1
    assert engine.scheduler.terminal_outcome(task_id)["status"] == "failed"


def test_allowed_policy_records_success_and_clears_in_flight(monkeypatch):
    engine = OrchestrationEngine(policy_runtime=PolicyRuntime())
    agent_id = engine.registry.register("policy-agent", "worker.processor")
    task_id = engine.scheduler.enqueue(
        {"target_agent": agent_id, "type": "policy"}
    )
    task = _dequeue(engine.scheduler)

    async def run_agent(agent, task):
        return {"ok": True}

    monkeypatch.setattr(engine, "_run_agent_task", run_agent)

    _run(engine._execute_task(task))

    outcome = engine.scheduler.terminal_outcome(task_id)
    assert outcome["status"] == "completed"
    assert outcome["result"] == {"ok": True}
    assert task_id not in engine.scheduler._in_flight
    assert engine.registry.get(agent_id)["status"] == AgentStatus.PAUSED.value


def test_policy_evaluator_exception_fails_closed():
    def evaluator(task):
        raise ConnectionError("policy service down")

    runtime = PolicyRuntime(evaluator=evaluator)

    assert runtime.authorize_task({"id": "task-1"}) == PolicyDecision(
        False,
        "policy_unavailable",
    )


def test_false_policy_evaluator_result_has_denial_reason():
    runtime = PolicyRuntime(evaluator=lambda task: False)

    assert runtime.authorize_task({"id": "task-1"}) == PolicyDecision(
        False,
        "policy_denied",
    )


def test_runtime_failure_retries_same_task_id_then_terminal(monkeypatch):
    engine = OrchestrationEngine(policy_runtime=PolicyRuntime())
    agent_id = engine.registry.register("retry-agent", "worker.processor")
    task_id = engine.scheduler.enqueue(
        {"target_agent": agent_id, "type": "retry"}
    )
    attempts = []

    async def fail_agent(agent, task):
        attempts.append(task["id"])
        raise RuntimeError("worker unavailable")

    monkeypatch.setattr(engine, "_run_agent_task", fail_agent)

    task = _dequeue(engine.scheduler)
    _run(engine._execute_task(task))
    assert engine.scheduler.terminal_outcome(task_id) is None
    assert engine.registry.get(agent_id)["status"] == AgentStatus.PAUSED.value

    task = _dequeue(engine.scheduler)
    assert task["id"] == task_id
    _run(engine._execute_task(task))
    assert engine.scheduler.terminal_outcome(task_id) is None

    task = _dequeue(engine.scheduler)
    assert task["id"] == task_id
    _run(engine._execute_task(task))

    outcome = engine.scheduler.terminal_outcome(task_id)
    assert outcome["status"] == "failed"
    assert outcome["reason"] == "worker unavailable"
    assert attempts == [task_id, task_id, task_id]
    assert engine.registry.get(agent_id)["status"] == AgentStatus.FAILED.value
