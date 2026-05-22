import pytest

from src.cli import build_parser


class TestCliParser:
    def test_build_parser_exposes_global_options(self):
        parser = build_parser()

        args = parser.parse_args([
            "--config",
            "local.yml",
            "--verbose",
            "status",
        ])

        assert args.config == "local.yml"
        assert args.verbose is True
        assert args.command == "status"

    def test_build_parser_exposes_subcommands(self):
        parser = build_parser()

        init_args = parser.parse_args(["init", "demo"])
        deploy_args = parser.parse_args(["deploy", "agent.yaml"])
        logs_args = parser.parse_args(["logs", "agent-1", "--tail", "25"])

        assert init_args.command == "init"
        assert init_args.name == "demo"
        assert deploy_args.command == "deploy"
        assert deploy_args.manifest == "agent.yaml"
        assert logs_args.command == "logs"
        assert logs_args.agent_id == "agent-1"
        assert logs_args.tail == 25

    def test_build_parser_exposes_status_watch_option(self):
        parser = build_parser()

        args = parser.parse_args(["status", "--watch"])

        assert args.command == "status"
        assert args.watch is True

    def test_build_parser_allows_inspection_without_cli_exit(self):
        parser = build_parser()

        args = parser.parse_args([])

        assert args.command is None

    def test_build_parser_returns_independent_parser_instances(self):
        first_parser = build_parser()
        second_parser = build_parser()

        assert first_parser is not second_parser
        assert first_parser.parse_args(["status"]).command == "status"
        assert (
            second_parser.parse_args(["logs", "agent-2"]).agent_id
            == "agent-2"
        )

    def test_parser_still_rejects_unknown_options(self):
        parser = build_parser()

        with pytest.raises(SystemExit):
            parser.parse_args(["--unknown"])
