import shutil

from src.agent.sandbox import AgentSandbox


def test_get_path_returns_existing_tracked_directory(tmp_path):
    sandbox = AgentSandbox(base_path=str(tmp_path))
    created = sandbox.create("agent-1")

    assert sandbox.get_path("agent-1") == created


def test_get_path_returns_none_for_unknown_agent(tmp_path):
    sandbox = AgentSandbox(base_path=str(tmp_path))

    assert sandbox.get_path("missing") is None


def test_get_path_drops_tracking_for_deleted_directory(tmp_path):
    sandbox = AgentSandbox(base_path=str(tmp_path))
    created = sandbox.create("agent-1")
    shutil.rmtree(created)

    assert sandbox.get_path("agent-1") is None
    assert "agent-1" not in sandbox._sandboxes


def test_get_path_drops_tracking_for_replaced_file(tmp_path):
    sandbox = AgentSandbox(base_path=str(tmp_path))
    created = sandbox.create("agent-1")
    shutil.rmtree(created)
    created.write_text("not a directory")

    assert sandbox.get_path("agent-1") is None
    assert "agent-1" not in sandbox._sandboxes


def test_get_path_drops_tracking_for_out_of_root_path(tmp_path):
    outside = tmp_path.parent / "outside-agent"
    outside.mkdir()
    try:
        sandbox = AgentSandbox(base_path=str(tmp_path / "root"))
        sandbox._sandboxes["agent-1"] = outside

        assert sandbox.get_path("agent-1") is None
        assert "agent-1" not in sandbox._sandboxes
    finally:
        shutil.rmtree(outside, ignore_errors=True)


def test_get_path_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    sandbox = AgentSandbox(base_path=str(root))
    created = sandbox.create("agent-1")
    shutil.rmtree(created)
    created.symlink_to(outside, target_is_directory=True)

    assert sandbox.get_path("agent-1") is None
    assert "agent-1" not in sandbox._sandboxes
