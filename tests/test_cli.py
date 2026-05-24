import json

from src.cli.main import main


def test_deploy_validates_worker_resource_requests(tmp_path, capsys):
    manifest = tmp_path / "worker.json"
    manifest.write_text(
        json.dumps(
            {
                "kind": "worker",
                "spec": {
                    "workload_class": "background",
                    "resources": {
                        "requests": {
                            "cpu": "250m",
                            "memory": "256Mi",
                        },
                    },
                },
            }
        )
    )

    assert main(["deploy", str(manifest)]) == 0
    output = capsys.readouterr()

    assert "Deploying agent from manifest" in output.out
    assert "Validated worker resources: class=background" in output.out
    assert output.err == ""


def test_deploy_rejects_worker_manifest_without_requests(tmp_path, capsys):
    manifest = tmp_path / "worker.json"
    manifest.write_text(
        json.dumps(
            {
                "kind": "worker",
                "spec": {"workload_class": "background"},
            }
        )
    )

    assert main(["deploy", str(manifest)]) == 2
    output = capsys.readouterr()

    assert output.out == ""
    assert "Deploy manifest validation failed" in output.err


def test_deploy_skips_resource_summary_for_non_worker_manifest(
    tmp_path,
    capsys,
):
    manifest = tmp_path / "api.json"
    manifest.write_text(json.dumps({"kind": "api", "spec": {}}))

    assert main(["deploy", str(manifest)]) == 0
    output = capsys.readouterr()

    assert "Deploying agent from manifest" in output.out
    assert "Validated worker resources" not in output.out
    assert output.err == ""
