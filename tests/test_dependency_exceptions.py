import importlib.util
import json
from pathlib import Path


def load_validator():
    script_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "validate_dependency_exceptions.py"
    )
    spec = importlib.util.spec_from_file_location(
        "validate_dependency_exceptions",
        script_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def valid_exception():
    return {
        "id": "GHSA-test-fastapi",
        "ecosystem": "pip",
        "package": "fastapi",
        "advisory": "GHSA-test",
        "owner": "@security-team",
        "reason": "Temporary upstream mitigation window",
        "expires_on": "2099-01-01",
    }


def test_valid_override_writes_summary_linking_manifest_record(
    tmp_path,
    monkeypatch,
    capsys,
):
    validator = load_validator()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    manifest = tmp_path / ".github" / "dependency-review-exceptions.json"
    overrides = tmp_path / "overrides.json"
    summary = tmp_path / "summary.md"
    write_json(manifest, {"version": 1, "exceptions": [valid_exception()]})
    write_json(
        overrides,
        {
            "overrides": [
                {
                    "ecosystem": "pip",
                    "package": "fastapi",
                    "advisory": "GHSA-test",
                }
            ]
        },
    )

    result = validator.main(
        [
            "--manifest",
            str(manifest),
            "--overrides",
            str(overrides),
            "--summary",
            str(summary),
            "--today",
            "2026-05-23",
        ]
    )

    assert result == 0
    assert "validated 1 exception records" in capsys.readouterr().out
    summary_text = summary.read_text(encoding="utf-8")
    assert "pip:fastapi" in summary_text
    assert "GHSA-test" in summary_text
    assert ".github/dependency-review-exceptions.json" in summary_text
    assert "example/repo/blob/abc123" in summary_text


def test_override_without_matching_manifest_record_fails(
    tmp_path,
    capsys,
):
    validator = load_validator()
    manifest = tmp_path / ".github" / "dependency-review-exceptions.json"
    overrides = tmp_path / "overrides.json"
    write_json(manifest, {"version": 1, "exceptions": []})
    write_json(
        overrides,
        {"overrides": [{"ecosystem": "pip", "package": "fastapi"}]},
    )

    result = validator.main(
        [
            "--manifest",
            str(manifest),
            "--overrides",
            str(overrides),
            "--today",
            "2026-05-23",
        ]
    )

    assert result == 1
    assert "no matching manifest record" in capsys.readouterr().err


def test_expired_exception_fails_until_removed_or_renewed(
    tmp_path,
    capsys,
):
    validator = load_validator()
    entry = valid_exception()
    entry["expires_on"] = "2026-05-22"
    manifest = tmp_path / ".github" / "dependency-review-exceptions.json"
    write_json(manifest, {"version": 1, "exceptions": [entry]})

    result = validator.main(
        [
            "--manifest",
            str(manifest),
            "--today",
            "2026-05-23",
        ]
    )

    assert result == 1
    assert "is expired" in capsys.readouterr().err


def test_manifest_requires_owner_reason_expiration_and_coordinates(
    tmp_path,
    capsys,
):
    validator = load_validator()
    manifest = tmp_path / ".github" / "dependency-review-exceptions.json"
    write_json(
        manifest,
        {
            "version": 1,
            "exceptions": [
                {
                    "id": "missing-fields",
                    "ecosystem": "pip",
                    "package": "",
                    "reason": "",
                }
            ],
        },
    )

    result = validator.main(
        [
            "--manifest",
            str(manifest),
            "--today",
            "2026-05-23",
        ]
    )

    assert result == 1
    error_text = capsys.readouterr().err
    assert "package must be a non-empty string" in error_text
    assert "owner must be a non-empty string" in error_text
    assert "expires_on must be a non-empty string" in error_text


def test_repository_manifest_is_valid_for_ci(tmp_path, capsys):
    validator = load_validator()
    repo_root = Path(__file__).resolve().parents[1]
    summary = tmp_path / "summary.md"

    result = validator.main(
        [
            "--manifest",
            str(repo_root / ".github/dependency-review-exceptions.json"),
            "--summary",
            str(summary),
        ]
    )

    assert result == 0
    assert "validated 0 exception records" in capsys.readouterr().out
    assert "No active dependency review exceptions" in summary.read_text(
        encoding="utf-8",
    )
