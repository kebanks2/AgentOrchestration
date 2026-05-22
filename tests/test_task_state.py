import pytest

from src.common.task_state import (
    TaskStateRepository,
    WorkspaceScopeRequired,
    postgres_workspace_policy,
)


def test_task_state_reads_are_scoped_by_workspace():
    repository = TaskStateRepository()
    repository.put("workspace-a", "task-123", "running")
    repository.put("workspace-b", "task-123", "queued")

    assert repository.get("workspace-a", "task-123").status == "running"
    assert repository.get("workspace-b", "task-123").status == "queued"


def test_task_state_writes_are_scoped_by_workspace():
    repository = TaskStateRepository()
    repository.put("workspace-a", "task-123", "running")
    repository.put("workspace-b", "task-123", "queued")

    repository.update(
        "workspace-a",
        "task-123",
        status="failed",
        payload={"reason": "timeout"},
    )

    assert repository.get("workspace-a", "task-123").status == "failed"
    assert repository.get("workspace-a", "task-123").payload == {
        "reason": "timeout",
    }
    assert repository.get("workspace-b", "task-123").status == "queued"


def test_task_state_delete_requires_workspace_scope():
    repository = TaskStateRepository()
    repository.put("workspace-a", "task-123", "running")
    repository.put("workspace-b", "task-123", "queued")

    assert repository.delete("workspace-a", "task-123") is True
    assert repository.get("workspace-a", "task-123") is None
    assert repository.get("workspace-b", "task-123").status == "queued"


def test_task_state_access_rejects_missing_workspace_scope():
    repository = TaskStateRepository()

    with pytest.raises(WorkspaceScopeRequired, match="workspace_id"):
        repository.put("", "task-123", "queued")

    with pytest.raises(WorkspaceScopeRequired, match="workspace_id"):
        repository.get(None, "task-123")


def test_unscoped_task_state_helpers_are_blocked():
    repository = TaskStateRepository()
    repository.put("workspace-a", "task-123", "running")

    with pytest.raises(WorkspaceScopeRequired, match="workspace_id"):
        repository.get_by_task_id("task-123")

    with pytest.raises(WorkspaceScopeRequired, match="workspace_id"):
        repository.update_by_task_id("task-123", status="failed")


def test_postgres_policy_enforces_workspace_predicate():
    policy = postgres_workspace_policy()

    assert "ENABLE ROW LEVEL SECURITY" in policy
    assert "workspace_id = current_setting('app.workspace_id', true)" in policy
    assert "WITH CHECK" in policy


def test_postgres_policy_rejects_unsafe_table_names():
    with pytest.raises(ValueError, match="table_name"):
        postgres_workspace_policy("task_state; drop table users")
