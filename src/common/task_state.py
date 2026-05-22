"""Workspace-scoped task state storage helpers."""

from dataclasses import dataclass, field
from time import time
from typing import Any, Dict, Optional, Tuple


class WorkspaceScopeRequired(ValueError):
    """Raised when task state access omits workspace scope."""


@dataclass
class TaskState:
    workspace_id: str
    task_id: str
    status: str
    payload: Dict[str, Any] = field(default_factory=dict)
    updated_at: float = field(default_factory=time)


class TaskStateRepository:
    def __init__(self):
        self._states: Dict[Tuple[str, str], TaskState] = {}

    def put(
        self,
        workspace_id: str,
        task_id: str,
        status: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> TaskState:
        workspace_id = _require_scope(workspace_id)
        task_id = _require_task_id(task_id)
        state = TaskState(
            workspace_id=workspace_id,
            task_id=task_id,
            status=status,
            payload=dict(payload or {}),
        )
        self._states[(workspace_id, task_id)] = state
        return state

    def get(self, workspace_id: str, task_id: str) -> Optional[TaskState]:
        workspace_id = _require_scope(workspace_id)
        task_id = _require_task_id(task_id)
        return self._states.get((workspace_id, task_id))

    def update(
        self,
        workspace_id: str,
        task_id: str,
        *,
        status: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[TaskState]:
        workspace_id = _require_scope(workspace_id)
        task_id = _require_task_id(task_id)
        state = self._states.get((workspace_id, task_id))
        if state is None:
            return None
        if status is not None:
            state.status = status
        if payload is not None:
            state.payload = dict(payload)
        state.updated_at = time()
        return state

    def delete(self, workspace_id: str, task_id: str) -> bool:
        workspace_id = _require_scope(workspace_id)
        task_id = _require_task_id(task_id)
        return self._states.pop((workspace_id, task_id), None) is not None

    def get_by_task_id(self, task_id: str):
        _require_task_id(task_id)
        raise WorkspaceScopeRequired(
            "Task state queries must include workspace_id"
        )

    def update_by_task_id(self, task_id: str, **_changes):
        _require_task_id(task_id)
        raise WorkspaceScopeRequired(
            "Task state updates must include workspace_id"
        )


def postgres_workspace_policy(table_name: str = "task_state") -> str:
    if not table_name.replace("_", "").isalnum():
        raise ValueError("table_name must contain only letters, digits, or _")
    workspace_predicate = (
        "workspace_id = current_setting('app.workspace_id', true)"
    )
    return (
        f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;\n"
        f"CREATE POLICY {table_name}_workspace_scope ON {table_name}\n"
        f"USING ({workspace_predicate})\n"
        f"WITH CHECK ({workspace_predicate});"
    )


def _require_scope(workspace_id: str) -> str:
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise WorkspaceScopeRequired("workspace_id is required")
    return workspace_id.strip()


def _require_task_id(task_id: str) -> str:
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id is required")
    return task_id.strip()
