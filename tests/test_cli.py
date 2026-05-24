import argparse
import sys

import pytest

from src.cli.main import cli, non_negative_int


def test_logs_rejects_negative_tail(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ao", "logs", "agent-1", "--tail", "-5"])

    with pytest.raises(SystemExit) as exc_info:
        cli()

    assert exc_info.value.code == 2
    assert "must be zero or a positive integer" in capsys.readouterr().err


def test_logs_accepts_zero_tail(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ao", "logs", "agent-1", "--tail", "0"])

    cli()

    assert "Fetching logs for agent: agent-1" in capsys.readouterr().out


def test_non_negative_int_requires_integer():
    with pytest.raises(argparse.ArgumentTypeError, match="must be an integer"):
        non_negative_int("one")
