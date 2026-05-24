"""Validate GitHub Actions runner provenance before build steps run."""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, Optional, Sequence, Set, Tuple


TRUE_VALUES = {"1", "true", "yes", "y", "on"}
DEFAULT_REQUIRED_SELF_HOSTED_LABELS = frozenset(
    {"self-hosted", "linux", "x64"}
)
DEFAULT_MAX_IMAGE_AGE_DAYS = 14


@dataclass(frozen=True)
class RunnerProvenance:
    name: str
    os: str
    arch: str
    environment: str
    labels: frozenset
    image_digest: str
    image_built_at: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "RunnerProvenance":
        labels = set()
        labels.update(_split_values(env.get("AO_RUNNER_LABELS", "")))
        labels.update(_split_values(env.get("RUNNER_LABELS", "")))
        labels.update(_split_values(env.get("RUNNER_OS", "")))
        labels.update(_split_values(env.get("RUNNER_ARCH", "")))
        labels.update(_split_values(env.get("AO_RUNNER_ENVIRONMENT", "")))
        labels.update(_split_values(env.get("RUNNER_ENVIRONMENT", "")))

        return cls(
            name=env.get("RUNNER_NAME", "unknown"),
            os=env.get("RUNNER_OS", "unknown"),
            arch=env.get("RUNNER_ARCH", "unknown"),
            environment=(
                env.get("AO_RUNNER_ENVIRONMENT")
                or env.get("RUNNER_ENVIRONMENT")
                or "unknown"
            ),
            labels=frozenset(labels),
            image_digest=(
                env.get("AO_RUNNER_IMAGE_DIGEST")
                or env.get("RUNNER_IMAGE_DIGEST")
                or ""
            ).strip(),
            image_built_at=(
                env.get("AO_RUNNER_IMAGE_BUILT_AT")
                or env.get("RUNNER_IMAGE_BUILT_AT")
                or ""
            ).strip(),
        )

    @property
    def is_self_hosted(self) -> bool:
        return _norm(self.environment) == "self-hosted"


@dataclass(frozen=True)
class RunnerPolicy:
    strict: bool
    required_labels: frozenset
    approved_digests: frozenset
    max_image_age_days: int

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        strict_override: bool = False,
        max_age_override: Optional[int] = None,
    ) -> "RunnerPolicy":
        strict = strict_override or _env_truthy(
            env, "AO_RUNNER_PREFLIGHT_STRICT"
        )
        environment = env.get("AO_RUNNER_ENVIRONMENT") or env.get(
            "RUNNER_ENVIRONMENT", ""
        )
        strict = strict or (_norm(environment) == "self-hosted")
        strict = strict or _is_release_ref(env)

        required = set(_split_values(env.get("AO_APPROVED_RUNNER_LABELS", "")))
        if strict and not required:
            required.update(DEFAULT_REQUIRED_SELF_HOSTED_LABELS)

        max_age = max_age_override
        if max_age is None:
            max_age = int(
                env.get("AO_RUNNER_IMAGE_MAX_AGE_DAYS")
                or DEFAULT_MAX_IMAGE_AGE_DAYS
            )

        return cls(
            strict=strict,
            required_labels=frozenset(required),
            approved_digests=frozenset(
                _split_values(env.get("AO_APPROVED_RUNNER_IMAGE_DIGESTS", ""))
            ),
            max_image_age_days=max_age,
        )


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "fail"


@dataclass(frozen=True)
class PreflightResult:
    strict: bool
    checks: Tuple[Check, ...]

    @property
    def passed(self) -> bool:
        return not any(check.failed for check in self.checks)


def validate_runner_provenance(
    provenance: RunnerProvenance,
    policy: RunnerPolicy,
    now: Optional[datetime] = None,
) -> PreflightResult:
    now = now or datetime.now(timezone.utc)
    checks = []

    missing_labels = policy.required_labels.difference(provenance.labels)
    if policy.strict and missing_labels:
        checks.append(
            Check(
                "runner labels",
                "fail",
                "missing required labels: "
                + ", ".join(sorted(missing_labels)),
            )
        )
    elif policy.required_labels:
        checks.append(
            Check(
                "runner labels",
                "pass",
                "required labels present: "
                + ", ".join(sorted(policy.required_labels)),
            )
        )
    else:
        checks.append(
            Check(
                "runner labels",
                "warn",
                "no required runner labels configured for report-only run",
            )
        )

    digest = provenance.image_digest
    if not digest:
        checks.append(
            Check(
                "image digest",
                "fail" if policy.strict else "warn",
                "runner image digest is not available",
            )
        )
    elif not digest.startswith("sha256:"):
        checks.append(
            Check(
                "image digest",
                "fail" if policy.strict else "warn",
                "runner image digest must use sha256:<hex> format",
            )
        )
    elif policy.approved_digests and digest not in policy.approved_digests:
        checks.append(
            Check(
                "image digest",
                "fail" if policy.strict else "warn",
                "runner image digest is not in the approved allowlist",
            )
        )
    elif policy.strict and not policy.approved_digests:
        checks.append(
            Check(
                "image digest",
                "fail",
                "strict mode requires AO_APPROVED_RUNNER_IMAGE_DIGESTS",
            )
        )
    else:
        checks.append(Check("image digest", "pass", digest))

    checks.extend(_validate_build_timestamp(provenance, policy, now))
    return PreflightResult(strict=policy.strict, checks=tuple(checks))


def render_summary(
    provenance: RunnerProvenance,
    policy: RunnerPolicy,
    result: PreflightResult,
) -> str:
    labels = ", ".join(sorted(provenance.labels)) or "none"
    outcome = "passed" if result.passed else "failed"
    mode = "strict" if policy.strict else "report-only"
    lines = [
        "## Runner provenance preflight",
        "",
        f"Status: **{outcome}** ({mode})",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Runner | `{_escape_table(provenance.name)}` |",
        f"| OS / arch | `{_escape_table(provenance.os)}` / "
        f"`{_escape_table(provenance.arch)}` |",
        f"| Environment | `{_escape_table(provenance.environment)}` |",
        f"| Labels | `{_escape_table(labels)}` |",
        f"| Image digest | "
        f"`{_escape_table(provenance.image_digest or 'missing')}` |",
        f"| Image built at | "
        f"`{_escape_table(provenance.image_built_at or 'missing')}` |",
        "",
        "| Check | Status | Detail |",
        "| --- | --- | --- |",
    ]

    for check in result.checks:
        lines.append(
            f"| {_escape_table(check.name)} | {check.status} | "
            f"{_escape_table(check.detail)} |"
        )

    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate runner image provenance and freshness."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail when provenance is missing, stale, or unapproved",
    )
    parser.add_argument(
        "--summary-file",
        default=os.environ.get("GITHUB_STEP_SUMMARY"),
        help="write a Markdown preflight summary to this file",
    )
    parser.add_argument(
        "--max-image-age-days",
        type=int,
        default=None,
        help="maximum allowed runner image age in strict mode",
    )
    args = parser.parse_args(argv)

    env = dict(os.environ)
    provenance = RunnerProvenance.from_env(env)
    policy = RunnerPolicy.from_env(
        env,
        strict_override=args.strict,
        max_age_override=args.max_image_age_days,
    )
    result = validate_runner_provenance(provenance, policy)
    summary = render_summary(provenance, policy, result)

    if args.summary_file:
        with open(args.summary_file, "a", encoding="utf-8") as handle:
            handle.write(summary)
    print(summary)

    return 0 if result.passed else 1


def _validate_build_timestamp(
    provenance: RunnerProvenance,
    policy: RunnerPolicy,
    now: datetime,
) -> Tuple[Check, ...]:
    value = provenance.image_built_at
    if not value:
        return (
            Check(
                "image build timestamp",
                "fail" if policy.strict else "warn",
                "runner image build timestamp is not available",
            ),
        )

    built_at = _parse_timestamp(value)
    if built_at is None:
        return (
            Check(
                "image build timestamp",
                "fail" if policy.strict else "warn",
                "runner image build timestamp must be ISO 8601",
            ),
        )

    if built_at > now + timedelta(minutes=5):
        return (
            Check(
                "image build timestamp",
                "fail" if policy.strict else "warn",
                "runner image build timestamp is in the future",
            ),
        )

    age = now - built_at
    max_age = timedelta(days=policy.max_image_age_days)
    if age > max_age:
        return (
            Check(
                "image build timestamp",
                "fail" if policy.strict else "warn",
                f"runner image is {age.days} days old; "
                f"maximum is {policy.max_image_age_days}",
            ),
        )

    return (
        Check(
            "image build timestamp",
            "pass",
            f"runner image age is {age.days} days",
        ),
    )


def _split_values(raw: str) -> Set[str]:
    raw = (raw or "").strip()
    if not raw:
        return set()

    values = _try_json_list(raw)
    if values is None:
        values = raw.replace("\n", ",").split(",")

    return {_norm(value) for value in values if _norm(value)}


def _try_json_list(raw: str) -> Optional[Iterable[str]]:
    if not raw.startswith("["):
        return None
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(loaded, list):
        return None
    return [str(item) for item in loaded]


def _parse_timestamp(value: str) -> Optional[datetime]:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_release_ref(env: Mapping[str, str]) -> bool:
    event = _norm(env.get("GITHUB_EVENT_NAME", ""))
    ref = env.get("GITHUB_REF", "")
    return event == "release" or ref.startswith("refs/tags/")


def _env_truthy(env: Mapping[str, str], key: str) -> bool:
    return _norm(env.get(key, "")) in TRUE_VALUES


def _norm(value: object) -> str:
    return str(value).strip().lower()


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    sys.exit(main())
