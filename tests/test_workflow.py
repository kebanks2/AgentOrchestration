from src.orchestrator.workflow import StepStatus, WorkflowManager, WorkflowStep


def test_preflight_rejects_missing_compensation_without_running_handlers():
    events = []
    manager = WorkflowManager()
    workflow = manager.create_workflow("requires-rollback")
    mutate = WorkflowStep(
        "mutate-state",
        lambda: events.append("mutate"),
        requires_compensation=True,
    )
    downstream = WorkflowStep("publish", lambda: events.append("publish"))

    workflow.add_step(mutate).add_step(downstream)

    assert manager.execute_workflow(workflow.id) is False
    assert workflow.status == StepStatus.PENDING
    assert mutate.status == StepStatus.PENDING
    assert downstream.status == StepStatus.PENDING
    assert events == []
    assert "compensating action" in workflow.validation_errors[0]
    assert workflow.audit_log[-1].startswith(
        "compensation validation rejected"
    )


def test_compensating_action_runs_and_blocks_downstream_after_failure():
    events = []
    manager = WorkflowManager()
    workflow = manager.create_workflow("partial-rollback")

    mutate = WorkflowStep(
        "mutate-state",
        lambda: events.append("mutate"),
        requires_compensation=True,
        compensating_action=lambda: events.append("undo-mutate"),
    )

    def fail_step():
        events.append("fail")
        raise RuntimeError("handler failed")

    failed = WorkflowStep("write-side-effect", fail_step)
    downstream = WorkflowStep("publish", lambda: events.append("publish"))

    workflow.add_step(mutate).add_step(failed).add_step(downstream)

    assert manager.execute_workflow(workflow.id) is False
    assert events == ["mutate", "fail", "undo-mutate"]
    assert workflow.status == StepStatus.ROLLED_BACK
    assert mutate.status == StepStatus.COMPENSATED
    assert failed.status == StepStatus.FAILED
    assert failed.error == "handler failed"
    assert downstream.status == StepStatus.BLOCKED
    assert downstream.error == "blocked after partial rollback"
    assert "compensated step mutate-state" in workflow.audit_log


def test_terminal_workflow_rejects_duplicate_execution_after_rollback():
    events = []
    manager = WorkflowManager()
    workflow = manager.create_workflow("no-duplicate-rollback")
    mutate = WorkflowStep(
        "mutate-state",
        lambda: events.append("mutate"),
        requires_compensation=True,
        compensating_action=lambda: events.append("undo-mutate"),
    )

    def fail_step():
        events.append("fail")
        raise RuntimeError("handler failed")

    workflow.add_step(mutate).add_step(WorkflowStep("fail", fail_step))

    assert manager.execute_workflow(workflow.id) is False
    assert workflow.status == StepStatus.ROLLED_BACK
    assert manager.execute_workflow(workflow.id) is False
    assert workflow.status == StepStatus.ROLLED_BACK
    assert events == ["mutate", "fail", "undo-mutate"]
    assert workflow.audit_log[-1] == (
        "duplicate execution rejected for rolled_back"
    )


def test_compensation_runs_in_reverse_completion_order():
    events = []
    manager = WorkflowManager()
    workflow = manager.create_workflow("reverse-rollback")
    first = WorkflowStep(
        "first",
        lambda: events.append("first"),
        requires_compensation=True,
        compensating_action=lambda: events.append("undo-first"),
    )
    second = WorkflowStep(
        "second",
        lambda: events.append("second"),
        requires_compensation=True,
        compensating_action=lambda: events.append("undo-second"),
    )

    def fail_step():
        events.append("fail")
        raise RuntimeError("handler failed")

    workflow.add_step(first).add_step(second).add_step(
        WorkflowStep("fail", fail_step)
    )

    assert manager.execute_workflow(workflow.id) is False
    assert events == [
        "first",
        "second",
        "fail",
        "undo-second",
        "undo-first",
    ]
    assert first.status == StepStatus.COMPENSATED
    assert second.status == StepStatus.COMPENSATED


def test_compensation_failure_fails_closed_and_blocks_downstream():
    events = []
    manager = WorkflowManager()
    workflow = manager.create_workflow("failed-rollback")

    def bad_compensation():
        events.append("undo")
        raise RuntimeError("undo failed")

    mutate = WorkflowStep(
        "mutate-state",
        lambda: events.append("mutate"),
        requires_compensation=True,
        compensating_action=bad_compensation,
    )
    failed = WorkflowStep(
        "fail",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    downstream = WorkflowStep("publish", lambda: events.append("publish"))

    workflow.add_step(mutate).add_step(failed).add_step(downstream)

    assert manager.execute_workflow(workflow.id) is False
    assert workflow.status == StepStatus.FAILED
    assert mutate.status == StepStatus.FAILED
    assert mutate.error == "undo failed"
    assert downstream.status == StepStatus.BLOCKED
    assert events == ["mutate", "undo"]
    assert workflow.audit_log[-1].startswith("partial rollback failed")


def test_unknown_dependency_rejected_before_lifecycle_changes():
    events = []
    manager = WorkflowManager()
    workflow = manager.create_workflow("bad-binding")
    step = WorkflowStep(
        "publish",
        lambda: events.append("publish"),
        depends_on=["missing-step"],
    )
    workflow.add_step(step)

    assert manager.execute_workflow(workflow.id) is False
    assert workflow.status == StepStatus.PENDING
    assert step.status == StepStatus.PENDING
    assert events == []
    assert "unknown step" in workflow.validation_errors[0]


def test_existing_workflow_without_compensation_requirement_still_runs():
    events = []
    manager = WorkflowManager()
    workflow = manager.create_workflow("simple")
    workflow.add_step(WorkflowStep("first", lambda: events.append("first")))
    workflow.add_step(WorkflowStep("second", lambda: events.append("second")))

    assert manager.execute_workflow(workflow.id) is True
    assert workflow.status == StepStatus.COMPLETED
    assert [step.status for step in workflow.steps] == [
        StepStatus.COMPLETED,
        StepStatus.COMPLETED,
    ]
    assert events == ["first", "second"]
