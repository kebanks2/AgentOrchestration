from src.orchestrator.workflow import (
    StepStatus,
    WorkflowManager,
    WorkflowStep,
)


def test_cancelled_parent_prevents_child_retry():
    manager = WorkflowManager()
    workflow = manager.create_workflow("parent-cancel")
    attempts = {"count": 0}

    def child_handler():
        attempts["count"] += 1
        manager.cancel_workflow(workflow.id)
        raise RuntimeError("transient child failure")

    step = WorkflowStep("child", child_handler, retries=3)
    workflow.add_step(step)

    assert manager.execute_workflow(workflow.id) is False
    assert attempts["count"] == 1
    assert workflow.status == StepStatus.CANCELLED
    assert step.status == StepStatus.CANCELLED
    assert step.error is None

    audit_events = [record["event"] for record in manager.audit_records()]
    assert "workflow_cancelled" in audit_events
    assert "cancelled_parent_retry_rejected" in audit_events


def test_cancelled_workflow_is_not_dispatched_later():
    manager = WorkflowManager()
    workflow = manager.create_workflow("already-cancelled")
    attempts = {"count": 0}
    workflow.add_step(
        WorkflowStep("child", lambda: attempts.__setitem__("count", 1))
    )

    assert manager.cancel_workflow(workflow.id) is True
    assert manager.execute_workflow(workflow.id) is False

    assert attempts["count"] == 0
    assert workflow.status == StepStatus.CANCELLED
    assert workflow.steps[0].status == StepStatus.CANCELLED

    audit_events = [record["event"] for record in manager.audit_records()]
    assert audit_events.count("workflow_cancelled") == 1
    assert "cancelled_workflow_dispatch_rejected" in audit_events


def test_parent_cancel_during_successful_child_does_not_complete_child():
    manager = WorkflowManager()
    workflow = manager.create_workflow("cancelled-during-handler")

    def child_handler():
        manager.cancel_workflow(workflow.id)
        return "should-not-commit"

    step = WorkflowStep("child", child_handler)
    workflow.add_step(step)

    assert manager.execute_workflow(workflow.id) is False
    assert workflow.status == StepStatus.CANCELLED
    assert step.status == StepStatus.CANCELLED
    assert step.result is None

    audit_events = [record["event"] for record in manager.audit_records()]
    assert "cancelled_parent_completion_rejected" in audit_events


def test_regular_retry_still_succeeds_without_parent_cancel():
    manager = WorkflowManager()
    workflow = manager.create_workflow("retry-ok")
    attempts = {"count": 0}

    def flaky_child():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("transient")
        return "ok"

    step = WorkflowStep("child", flaky_child, retries=1)
    workflow.add_step(step)

    assert manager.execute_workflow(workflow.id) is True
    assert attempts["count"] == 2
    assert workflow.status == StepStatus.COMPLETED
    assert step.status == StepStatus.COMPLETED
    assert step.result == "ok"

    audit_events = [record["event"] for record in manager.audit_records()]
    assert "step_retry_scheduled" in audit_events
    assert "cancelled_parent_retry_rejected" not in audit_events
