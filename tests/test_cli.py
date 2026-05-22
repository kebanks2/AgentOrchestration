import sys

from src.cli import main


def test_status_watch_keyboard_interrupt_exits_cleanly(monkeypatch, capsys):
    def interrupt_sleep(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(sys, "argv", ["ao", "status", "--watch"])
    monkeypatch.setattr(main.time, "sleep", interrupt_sleep)

    exit_code = main.cli()

    captured = capsys.readouterr()
    assert exit_code == main.STATUS_INTERRUPT_EXIT_CODE
    assert "Checking agent status..." in captured.out
    assert "Status watch interrupted; exiting cleanly." in captured.err
    assert "Traceback" not in captured.err


def test_status_returns_success(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ao", "status"])

    exit_code = main.cli()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "Checking agent status...\n"
