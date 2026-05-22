import importlib.util
from importlib import metadata
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_sbom.py"
_SPEC = importlib.util.spec_from_file_location("generate_sbom", _SCRIPT)
generate_sbom = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(generate_sbom)


def _write_pyproject(path, dependencies):
    rendered = "\n".join(f'    "{dependency}",' for dependency in dependencies)
    path.write_text(
        "[project]\n"
        'name = "demo"\n'
        "dependencies = [\n"
        f"{rendered}\n"
        "]\n",
        encoding="utf-8",
    )


def test_strict_sbom_includes_platform_and_package_manager_metadata(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    _write_pyproject(pyproject, ["fastapi>=0.104.0"])

    sbom = generate_sbom.build_sbom(
        pyproject,
        strict=True,
        target_platform="linux-x86_64",
        package_manager_version="uv 1.2.3",
        version_provider=lambda name: {"fastapi": "0.104.1"}[name],
    )

    properties = {
        item["name"]: item["value"]
        for item in sbom["metadata"]["properties"]
    }
    assert properties["agent-orchestrator:target-platform"] == "linux-x86_64"
    assert (
        properties["agent-orchestrator:package-manager-version"]
        == "uv 1.2.3"
    )
    assert properties["agent-orchestrator:strict-resolution"] == "true"
    assert sbom["components"][0]["name"] == "fastapi"
    assert sbom["components"][0]["version"] == "0.104.1"


def test_strict_sbom_fails_on_unresolved_dependencies(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    _write_pyproject(pyproject, ["platform-only-package>=1"])

    def missing(_name):
        raise metadata.PackageNotFoundError

    with pytest.raises(generate_sbom.DependencyResolutionError) as exc:
        generate_sbom.build_sbom(
            pyproject,
            strict=True,
            version_provider=missing,
        )

    assert exc.value.unresolved == ["platform-only-package"]


def test_non_strict_sbom_marks_unresolved_dependency(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    _write_pyproject(pyproject, ["optional-package>=1"])

    def missing(_name):
        raise metadata.PackageNotFoundError

    sbom = generate_sbom.build_sbom(
        pyproject,
        strict=False,
        version_provider=missing,
    )

    component = sbom["components"][0]
    assert component["name"] == "optional-package"
    assert component["version"] == "unresolved"
    assert component["properties"] == [
        {
            "name": "agent-orchestrator:dependency-resolution",
            "value": "unresolved",
        }
    ]


def test_release_workflow_template_disables_warning_only_publication():
    workflow = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "release-sbom-workflow.yml"
    ).read_text(encoding="utf-8")

    assert "--strict" in workflow
    assert "--allow-unresolved" not in workflow
    assert "continue-on-error" not in workflow
    assert "|| true" not in workflow
    assert "if-no-files-found: error" in workflow


def test_release_sbom_make_target_is_strict():
    makefile = (
        Path(__file__).resolve().parents[1] / "Makefile"
    ).read_text(encoding="utf-8")

    assert "release-sbom:" in makefile
    assert "scripts/generate_sbom.py --strict" in makefile
