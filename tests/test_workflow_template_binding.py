import logging

from src.orchestrator.workflow import StepStatus, WorkflowManager, WorkflowStep


ERROR = "unresolved template variables in workflow parameters"


def test_unresolved_template_variables_do_not_start_workflow(caplog):
    manager = WorkflowManager()
    workflow = manager.create_workflow("deploy")
    called = False

    def deploy(**kwargs):
        nonlocal called
        called = True

    step = WorkflowStep(
        "deploy-prod",
        deploy,
        parameters={
            "environment": "{{ environment }}",
            "credential": "${credential}",
        },
    )
    workflow.add_step(step)

    caplog.set_level(logging.WARNING, logger="src.orchestrator.workflow")

    result = manager.execute_workflow(
        workflow.id,
        {"credential": "runtime-value"},
    )

    assert result is False
    assert called is False
    assert workflow.status is StepStatus.PENDING
    assert step.status is StepStatus.PENDING
    assert workflow.error == ERROR
    assert step.error == ERROR
    assert ERROR in caplog.text
    assert "environment" not in caplog.text
    assert "runtime-value" not in caplog.text


def test_runtime_parameters_are_rendered_and_passed_to_handler():
    manager = WorkflowManager()
    workflow = manager.create_workflow("deploy")
    received = {}

    def deploy(**kwargs):
        received.update(kwargs)
        return kwargs["environment"]

    step = WorkflowStep(
        "deploy-prod",
        deploy,
        parameters={
            "environment": "{{ environment }}",
            "metadata": {
                "endpoint": "https://${host}/workflow",
                "regions": ["${region}", "backup"],
            },
            "retries": 2,
        },
    )
    workflow.add_step(step)

    result = manager.execute_workflow(
        workflow.id,
        {
            "environment": "prod",
            "host": "api.example.test",
            "region": "us-east-1",
        },
    )

    assert result is True
    assert workflow.status is StepStatus.COMPLETED
    assert step.status is StepStatus.COMPLETED
    assert step.result == "prod"
    assert received == {
        "environment": "prod",
        "metadata": {
            "endpoint": "https://api.example.test/workflow",
            "regions": ["us-east-1", "backup"],
        },
        "retries": 2,
    }


def test_no_parameter_workflow_remains_compatible():
    manager = WorkflowManager()
    workflow = manager.create_workflow("maintenance")
    calls = []

    def cleanup():
        calls.append("ran")
        return "ok"

    step = WorkflowStep("cleanup", cleanup)
    workflow.add_step(step)

    assert manager.execute_workflow(workflow.id) is True
    assert calls == ["ran"]
    assert workflow.status is StepStatus.COMPLETED
    assert step.status is StepStatus.COMPLETED
    assert step.result == "ok"
