from datetime import datetime, timedelta, timezone

from src.ci.runner_provenance import (
    RunnerPolicy,
    RunnerProvenance,
    main,
    render_summary,
    validate_runner_provenance,
)


NOW = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)


def test_hosted_runner_is_report_only_when_metadata_is_missing():
    provenance = RunnerProvenance.from_env(
        {
            "RUNNER_NAME": "github-hosted-1",
            "RUNNER_OS": "Linux",
            "RUNNER_ARCH": "X64",
            "RUNNER_ENVIRONMENT": "github-hosted",
        }
    )
    policy = RunnerPolicy.from_env({})

    result = validate_runner_provenance(provenance, policy, now=NOW)

    assert result.passed
    assert not result.strict
    assert [check.status for check in result.checks] == [
        "warn",
        "warn",
        "warn",
    ]


def test_strict_self_hosted_runner_passes_with_approved_fresh_image():
    built_at = (NOW - timedelta(days=2)).isoformat()
    digest = "sha256:" + ("a" * 64)
    provenance = RunnerProvenance.from_env(
        {
            "RUNNER_NAME": "builder-1",
            "RUNNER_OS": "Linux",
            "RUNNER_ARCH": "X64",
            "RUNNER_ENVIRONMENT": "self-hosted",
            "AO_RUNNER_LABELS": "self-hosted,linux,x64,build",
            "AO_RUNNER_IMAGE_DIGEST": digest,
            "AO_RUNNER_IMAGE_BUILT_AT": built_at,
        }
    )
    policy = RunnerPolicy.from_env(
        {
            "RUNNER_ENVIRONMENT": "self-hosted",
            "AO_APPROVED_RUNNER_LABELS": "self-hosted,linux,x64",
            "AO_APPROVED_RUNNER_IMAGE_DIGESTS": digest,
        }
    )

    result = validate_runner_provenance(provenance, policy, now=NOW)

    assert result.passed
    assert result.strict
    assert all(check.status == "pass" for check in result.checks)


def test_strict_runner_fails_on_unapproved_digest_before_build():
    digest = "sha256:" + ("b" * 64)
    provenance = RunnerProvenance.from_env(
        {
            "RUNNER_ENVIRONMENT": "self-hosted",
            "AO_RUNNER_LABELS": "self-hosted,linux,x64",
            "AO_RUNNER_IMAGE_DIGEST": digest,
            "AO_RUNNER_IMAGE_BUILT_AT": NOW.isoformat(),
        }
    )
    policy = RunnerPolicy.from_env(
        {
            "RUNNER_ENVIRONMENT": "self-hosted",
            "AO_APPROVED_RUNNER_LABELS": "self-hosted,linux,x64",
            "AO_APPROVED_RUNNER_IMAGE_DIGESTS": "sha256:" + ("c" * 64),
        }
    )

    result = validate_runner_provenance(provenance, policy, now=NOW)

    assert not result.passed
    assert any("allowlist" in check.detail for check in result.checks)


def test_strict_runner_fails_on_stale_image():
    digest = "sha256:" + ("d" * 64)
    provenance = RunnerProvenance.from_env(
        {
            "RUNNER_ENVIRONMENT": "self-hosted",
            "AO_RUNNER_LABELS": "self-hosted,linux,x64",
            "AO_RUNNER_IMAGE_DIGEST": digest,
            "AO_RUNNER_IMAGE_BUILT_AT": (
                NOW - timedelta(days=15)
            ).isoformat(),
        }
    )
    policy = RunnerPolicy.from_env(
        {
            "RUNNER_ENVIRONMENT": "self-hosted",
            "AO_APPROVED_RUNNER_LABELS": "self-hosted,linux,x64",
            "AO_APPROVED_RUNNER_IMAGE_DIGESTS": digest,
            "AO_RUNNER_IMAGE_MAX_AGE_DAYS": "14",
        }
    )

    result = validate_runner_provenance(provenance, policy, now=NOW)

    assert not result.passed
    assert any("15 days old" in check.detail for check in result.checks)


def test_release_event_enables_strict_runner_preflight():
    policy = RunnerPolicy.from_env(
        {
            "GITHUB_EVENT_NAME": "release",
            "RUNNER_ENVIRONMENT": "github-hosted",
        }
    )

    assert policy.strict
    assert policy.required_labels == frozenset({"self-hosted", "linux", "x64"})


def test_summary_includes_provenance_details():
    digest = "sha256:" + ("e" * 64)
    provenance = RunnerProvenance.from_env(
        {
            "RUNNER_NAME": "release-builder",
            "RUNNER_OS": "Linux",
            "RUNNER_ARCH": "X64",
            "RUNNER_ENVIRONMENT": "self-hosted",
            "AO_RUNNER_LABELS": "self-hosted,linux,x64",
            "AO_RUNNER_IMAGE_DIGEST": digest,
            "AO_RUNNER_IMAGE_BUILT_AT": NOW.isoformat(),
        }
    )
    policy = RunnerPolicy.from_env(
        {
            "RUNNER_ENVIRONMENT": "self-hosted",
            "AO_APPROVED_RUNNER_LABELS": "self-hosted,linux,x64",
            "AO_APPROVED_RUNNER_IMAGE_DIGESTS": digest,
        }
    )
    result = validate_runner_provenance(provenance, policy, now=NOW)

    summary = render_summary(provenance, policy, result)

    assert "release-builder" in summary
    assert digest in summary
    assert "Status: **passed** (strict)" in summary


def test_cli_writes_summary_and_returns_failure_for_bad_strict_env(
    monkeypatch,
    tmp_path,
):
    summary_file = tmp_path / "summary.md"
    monkeypatch.setenv("AO_RUNNER_PREFLIGHT_STRICT", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "self-hosted")
    monkeypatch.setenv("AO_RUNNER_LABELS", "self-hosted,linux,x64")
    monkeypatch.delenv("AO_RUNNER_IMAGE_DIGEST", raising=False)
    monkeypatch.delenv("AO_RUNNER_IMAGE_BUILT_AT", raising=False)

    exit_code = main(["--summary-file", str(summary_file)])

    assert exit_code == 1
    assert "Runner provenance preflight" in summary_file.read_text()
