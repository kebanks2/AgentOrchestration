from src.cli import main as cli_main


def test_cli_expands_user_config_path_before_loading(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    config_path = home / "ao" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"app": {"name": "tilde"}}', encoding="utf-8")
    loaded_paths = []

    class RecordingConfig:
        def __init__(self, config_path=None):
            loaded_paths.append(config_path)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(cli_main, "Config", RecordingConfig)

    cli_main.cli(["--config", "~/ao/config.json", "status"])

    assert loaded_paths == [str(config_path.resolve())]


def test_cli_resolves_relative_config_path_before_loading(
    tmp_path,
    monkeypatch,
):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"app": {"name": "relative"}}', encoding="utf-8")
    loaded_paths = []

    class RecordingConfig:
        def __init__(self, config_path=None):
            loaded_paths.append(config_path)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "Config", RecordingConfig)

    cli_main.cli(["--config", "config.json", "status"])

    assert loaded_paths == [str(config_path.resolve())]


def test_load_cli_config_reads_expanded_user_path(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config_path = home / "ao" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"app": {"name": "loaded"}}', encoding="utf-8")

    monkeypatch.setenv("HOME", str(home))

    config = cli_main.load_cli_config("~/ao/config.json")

    assert config.get("app.name") == "loaded"


def test_load_cli_config_without_path_uses_default_config(monkeypatch):
    loaded_paths = []

    class RecordingConfig:
        def __init__(self, config_path=None):
            loaded_paths.append(config_path)

    monkeypatch.setattr(cli_main, "Config", RecordingConfig)

    cli_main.load_cli_config(None)

    assert loaded_paths == [None]
