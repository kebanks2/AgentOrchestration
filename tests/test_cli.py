from src.cli.main import cli


def test_deploy_success_returns_zero(capsys):
    calls = []

    def deployer(manifest):
        calls.append(manifest)

    exit_code = cli(["deploy", "agent.yaml"], deployer=deployer)

    assert exit_code == 0
    assert calls == ["agent.yaml"]
    assert capsys.readouterr().err == ""


def test_deploy_backend_failure_returns_nonzero(capsys):
    def deployer(manifest):
        raise ConnectionError("orchestrator unavailable")

    exit_code = cli(["deploy", "agent.yaml"], deployer=deployer)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Deploy failed: orchestrator unavailable" in captured.err


def test_missing_command_returns_nonzero(capsys):
    exit_code = cli([])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Agent Orchestrator CLI" in captured.out
