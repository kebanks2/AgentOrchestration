import pytest

from src.agent.sandbox import AgentSandbox


def test_safe_child_path_returns_nested_path(tmp_path):
    sandbox = AgentSandbox(base_path=str(tmp_path))
    root = sandbox.create("agent-1")

    child = sandbox.safe_child_path("agent-1", "logs", "run.json")

    assert child == root.resolve() / "logs" / "run.json"


def test_safe_child_path_rejects_parent_traversal(tmp_path):
    sandbox = AgentSandbox(base_path=str(tmp_path))
    sandbox.create("agent-1")

    with pytest.raises(ValueError, match="escapes"):
        sandbox.safe_child_path("agent-1", "..", "outside.txt")


def test_safe_child_path_rejects_absolute_paths(tmp_path):
    sandbox = AgentSandbox(base_path=str(tmp_path))
    sandbox.create("agent-1")

    with pytest.raises(ValueError, match="relative"):
        sandbox.safe_child_path("agent-1", str(tmp_path / "outside.txt"))


def test_safe_child_path_rejects_symlink_escape(tmp_path):
    sandbox = AgentSandbox(base_path=str(tmp_path / "sandboxes"))
    root = sandbox.create("agent-1")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes"):
        sandbox.safe_child_path("agent-1", "escape", "file.txt")


def test_safe_child_path_rejects_unknown_agent(tmp_path):
    sandbox = AgentSandbox(base_path=str(tmp_path))

    with pytest.raises(KeyError, match="agent-1"):
        sandbox.safe_child_path("agent-1", "logs", "run.json")
