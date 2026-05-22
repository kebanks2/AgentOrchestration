import pytest

from src.orchestrator.workflow import (
    StepStatus,
    WorkflowManager,
    WorkflowParameterError,
    WorkflowStep,
    merge_parameters,
)


def test_explicit_false_default_is_preserved():
    step = WorkflowStep(
        "gate",
        lambda params: params,
        parameter_defaults={"enabled": False},
        required_parameters=["enabled"],
    )

    bound = step.bind_parameters()

    assert bound["enabled"] is False
    assert step.parameter_decisions[-1] == {
        "action": "parameters_bound",
        "reason": "accepted",
        "keys": ["enabled"],
    }


def test_explicit_false_override_is_preserved_over_true_default():
    bound = merge_parameters(
        {"enabled": True},
        {"enabled": False},
        ["enabled"],
    )

    assert bound["enabled"] is False


def test_missing_required_parameter_is_rejected_without_raw_values():
    step = WorkflowStep(
        "gate",
        lambda: None,
        parameter_defaults={},
        required_parameters=["enabled"],
    )

    with pytest.raises(WorkflowParameterError):
        step.bind_parameters()

    assert step.parameter_decisions[-1] == {
        "action": "parameters_rejected",
        "reason": "missing_required",
        "keys": ["enabled"],
    }
    assert step.bound_parameters == {}


def test_workflow_execution_passes_explicit_false_to_handler():
    manager = WorkflowManager()
    workflow = manager.create_workflow("release")
    seen = []

    workflow.add_step(
        WorkflowStep(
            "gate",
            lambda params: seen.append(params["enabled"]) or "ok",
            parameter_defaults={"enabled": False},
            required_parameters=["enabled"],
        )
    )

    assert manager.execute_workflow(workflow.id)
    assert seen == [False]
    assert workflow.status == StepStatus.COMPLETED
    assert workflow.steps[0].result == "ok"


def test_workflow_rejects_missing_parameter_before_handler_runs():
    manager = WorkflowManager()
    workflow = manager.create_workflow("release")
    seen = []

    workflow.add_step(
        WorkflowStep(
            "gate",
            lambda params: seen.append(params),
            required_parameters=["enabled"],
        )
    )

    assert not manager.execute_workflow(workflow.id)
    assert seen == []
    assert workflow.status == StepStatus.FAILED
    assert workflow.steps[0].status == StepStatus.FAILED
