"""Workflow Manager — Defines and executes multi-step agent workflows."""

import logging
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    COMPENSATED = "compensated"
    ROLLED_BACK = "rolled_back"


class WorkflowStep:
    def __init__(
        self,
        name: str,
        handler: Callable,
        retries: int = 0,
        timeout: int = 300,
        requires_compensation: bool = False,
        compensating_action: Optional[Callable] = None,
        depends_on: Optional[List[str]] = None,
    ):
        self.id = str(uuid4())
        self.name = name
        self.handler = handler
        self.retries = retries
        self.timeout = timeout
        self.requires_compensation = requires_compensation
        self.compensating_action = compensating_action
        self.depends_on = list(depends_on or [])
        self.status = StepStatus.PENDING
        self.result: Any = None
        self.error: Optional[str] = None

    def set_compensating_action(self, action: Callable) -> "WorkflowStep":
        self.compensating_action = action
        return self

    def require_compensation(self) -> "WorkflowStep":
        self.requires_compensation = True
        return self

    def add_dependency(self, step: "WorkflowStep") -> "WorkflowStep":
        self.depends_on.append(step.id)
        return self


class Workflow:
    def __init__(self, name: str, description: str = ""):
        self.id = str(uuid4())
        self.name = name
        self.description = description
        self.steps: List[WorkflowStep] = []
        self._step_map: Dict[str, WorkflowStep] = {}
        self.status = StepStatus.PENDING
        self.validation_errors: List[str] = []
        self.audit_log: List[str] = []

    def add_step(self, step: WorkflowStep) -> "Workflow":
        self.steps.append(step)
        self._step_map[step.id] = step
        return self

    def get_step(self, step_id: str) -> Optional[WorkflowStep]:
        return self._step_map.get(step_id)


class WorkflowManager:
    def __init__(self):
        self._workflows: Dict[str, Workflow] = {}

    def create_workflow(self, name: str, description: str = "") -> Workflow:
        workflow = Workflow(name, description)
        self._workflows[workflow.id] = workflow
        return workflow

    def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        return self._workflows.get(workflow_id)

    def list_workflows(self) -> List[Workflow]:
        return list(self._workflows.values())

    def delete_workflow(self, workflow_id: str) -> bool:
        return self._workflows.pop(workflow_id, None) is not None

    def execute_workflow(self, workflow_id: str) -> bool:
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            return False

        if workflow.status in {
            StepStatus.COMPLETED,
            StepStatus.FAILED,
            StepStatus.ROLLED_BACK,
        }:
            message = (
                "duplicate execution rejected for "
                f"{workflow.status.value}"
            )
            workflow.audit_log.append(message)
            logger.warning("Workflow %s %s", workflow.id, message)
            return False

        if not self._validate_compensation_plan(workflow):
            return False

        workflow.status = StepStatus.RUNNING
        for index, step in enumerate(workflow.steps):
            step.status = StepStatus.RUNNING
            try:
                result = step.handler()
                step.result = result
                step.status = StepStatus.COMPLETED
            except Exception as e:
                step.error = str(e)
                step.status = StepStatus.FAILED
                self._rollback_completed_steps(workflow, index)
                return False

        workflow.status = StepStatus.COMPLETED
        return True

    def _validate_compensation_plan(self, workflow: Workflow) -> bool:
        workflow.validation_errors = []
        known_steps = {
            identifier
            for step in workflow.steps
            for identifier in (step.id, step.name)
        }

        for step in workflow.steps:
            for dependency in step.depends_on:
                if dependency not in known_steps:
                    workflow.validation_errors.append(
                        (
                            f"step {step.name} depends on unknown "
                            f"step {dependency}"
                        )
                    )

        for index, step in enumerate(workflow.steps[:-1]):
            has_downstream = any(
                downstream.depends_on == []
                or step.id in downstream.depends_on
                or step.name in downstream.depends_on
                for downstream in workflow.steps[index + 1:]
            )
            if (
                step.requires_compensation
                and has_downstream
                and not callable(step.compensating_action)
            ):
                workflow.validation_errors.append(
                    f"step {step.name} can affect downstream work but has no "
                    "compensating action"
                )

        if workflow.validation_errors:
            message = "; ".join(workflow.validation_errors)
            workflow.audit_log.append(
                f"compensation validation rejected: {message}"
            )
            logger.warning("Workflow %s rejected: %s", workflow.id, message)
            return False
        return True

    def _rollback_completed_steps(
        self,
        workflow: Workflow,
        failed_index: int,
    ) -> None:
        blocked_reason = "blocked after partial rollback"
        rollback_errors = []

        for step in reversed(workflow.steps[:failed_index]):
            if not step.requires_compensation:
                continue
            if not callable(step.compensating_action):
                step.error = "missing compensating action"
                rollback_errors.append(
                    f"{step.name}: missing compensating action"
                )
                continue
            try:
                step.compensating_action()
                step.status = StepStatus.COMPENSATED
                workflow.audit_log.append(f"compensated step {step.name}")
            except Exception as e:
                step.error = str(e)
                step.status = StepStatus.FAILED
                rollback_errors.append(f"{step.name}: {e}")

        for step in workflow.steps[failed_index + 1:]:
            if step.status == StepStatus.PENDING:
                step.status = StepStatus.BLOCKED
                step.error = blocked_reason

        if rollback_errors:
            workflow.status = StepStatus.FAILED
            message = "; ".join(rollback_errors)
            workflow.audit_log.append(f"partial rollback failed: {message}")
            logger.warning(
                "Workflow %s rollback failed: %s",
                workflow.id,
                message,
            )
            return

        workflow.status = StepStatus.ROLLED_BACK
        workflow.audit_log.append(blocked_reason)
        logger.warning("Workflow %s %s", workflow.id, blocked_reason)

# 2019-03-27T19:58:07 update

# 2019-05-09T09:42:56 update

# 2019-12-03T10:07:42 update

# 2020-01-16T18:43:28 update

# 2020-03-20T10:40:15 update

# 2020-04-17T15:36:50 update

# 2020-05-04T14:44:01 update

# 2020-06-16T13:17:31 update

# 2020-08-05T17:00:24 update

# 2020-09-04T08:29:23 update

# 2020-09-09T17:52:02 update

# 2020-10-23T10:57:44 update

# 2020-12-05T20:55:47 update

# 2021-01-15T19:23:40 update

# 2021-02-03T20:43:12 update

# 2021-03-16T12:26:47 update

# 2021-04-20T14:33:28 update

# 2021-10-14T15:03:32 update

# 2021-10-21T17:24:55 update

# 2021-11-16T17:01:08 update

# 2021-11-22T09:51:21 update

# 2021-12-21T16:15:47 update

# 2022-03-23T16:52:27 update

# 2022-12-21T09:25:50 update

# 2023-01-09T09:55:25 update

# 2023-01-13T11:06:15 update

# 2023-01-26T11:00:59 update

# 2023-02-23T08:56:54 update

# 2023-05-17T08:07:16 update

# 2023-06-06T17:09:34 update

# 2023-06-13T10:35:28 update

# 2023-08-24T20:36:06 update

# 2023-10-30T19:10:13 update

# 2024-01-02T08:27:25 update

# 2024-01-24T12:13:15 update

# 2024-02-08T13:35:49 update

# 2024-05-07T16:09:24 update

# 2024-05-11T09:48:46 update

# 2024-05-21T19:25:41 update

# 2024-06-05T12:00:30 update

# 2024-06-25T09:40:26 update

# 2024-09-17T13:49:39 update

# 2024-10-14T17:39:35 update

# 2024-11-27T20:14:35 update

# 2024-12-25T19:31:41 update

# 2025-01-16T13:15:09 update

# 2025-02-05T14:06:59 update

# 2025-02-17T20:55:11 update

# 2025-04-30T19:36:53 update

# 2025-07-17T10:14:40 update

# 2025-08-29T12:13:15 update

# 2025-09-03T13:51:11 update

# 2025-09-19T16:08:24 update

# 2025-11-27T08:38:12 update

# 2026-01-27T13:23:38 update

# 2026-01-28T11:22:50 update
