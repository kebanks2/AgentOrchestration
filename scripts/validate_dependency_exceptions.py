#!/usr/bin/env python3
"""Validate tracked dependency review exception records."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


DEFAULT_MANIFEST = ".github/dependency-review-exceptions.json"
REQUIRED_FIELDS = (
    "id",
    "ecosystem",
    "package",
    "owner",
    "reason",
    "expires_on",
)


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def entries_from_json(data: Any, label: str) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        entries = data.get("exceptions", data.get("overrides", []))
    else:
        raise ValueError(f"{label} must be a list or object")

    if not isinstance(entries, list):
        raise ValueError(f"{label} entries must be a list")
    if not all(isinstance(entry, dict) for entry in entries):
        raise ValueError(f"{label} entries must be objects")
    return entries


def parse_date(
    value: Any,
    entry_id: str,
    errors: List[str],
) -> Optional[dt.date]:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{entry_id}: expires_on must be a non-empty date")
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        errors.append(f"{entry_id}: expires_on must use YYYY-MM-DD format")
        return None


def require_text(
    entry: Mapping[str, Any],
    field: str,
    entry_id: str,
    errors: List[str],
) -> Optional[str]:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{entry_id}: {field} must be a non-empty string")
        return None
    return value.strip()


def exception_key(entry: Mapping[str, Any]) -> Tuple[str, str, str]:
    ecosystem = str(entry.get("ecosystem", "")).strip().lower()
    package = str(entry.get("package", "")).strip().lower()
    advisory = str(entry.get("advisory", "")).strip().lower()
    return ecosystem, package, advisory


def validate_manifest(
    manifest_data: Any,
    today: dt.date,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    errors: List[str] = []
    entries = entries_from_json(manifest_data, "manifest")
    valid_entries: List[Dict[str, Any]] = []
    seen_ids = set()
    seen_keys = set()

    for index, entry in enumerate(entries, start=1):
        entry_id = str(entry.get("id") or f"entry {index}")
        normalized: Dict[str, Any] = dict(entry)

        for field in REQUIRED_FIELDS:
            text = require_text(entry, field, entry_id, errors)
            if text is not None:
                normalized[field] = text

        expires_on = parse_date(entry.get("expires_on"), entry_id, errors)
        if expires_on is not None:
            normalized["expires_on_date"] = expires_on
            if expires_on < today:
                errors.append(
                    f"{entry_id}: expires_on {expires_on.isoformat()} "
                    "is expired"
                )

        if normalized.get("id") in seen_ids:
            errors.append(f"{entry_id}: duplicate exception id")
        seen_ids.add(normalized.get("id"))

        key = exception_key(normalized)
        if key in seen_keys:
            errors.append(f"{entry_id}: duplicate dependency coordinates")
        seen_keys.add(key)

        valid_entries.append(normalized)

    return valid_entries, errors


def validate_overrides(
    overrides_data: Any,
    manifest_entries: Sequence[Mapping[str, Any]],
) -> List[str]:
    errors: List[str] = []
    overrides = entries_from_json(overrides_data, "overrides")
    active_records = {
        exception_key(entry): entry for entry in manifest_entries
    }

    for index, override in enumerate(overrides, start=1):
        override_id = str(override.get("id") or f"override {index}")
        for field in ("ecosystem", "package"):
            require_text(override, field, override_id, errors)
        key = exception_key(override)
        if key not in active_records:
            errors.append(
                f"{override_id}: dependency review override has no "
                "matching manifest record"
            )

    return errors


def record_line_numbers(
    manifest_path: Path,
    entries: Sequence[Mapping[str, Any]],
) -> Dict[str, int]:
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    result: Dict[str, int] = {}
    for entry in entries:
        entry_id = str(entry.get("id", ""))
        needle = f'"id": "{entry_id}"'
        for line_number, line in enumerate(lines, start=1):
            if needle in line:
                result[entry_id] = line_number
                break
    return result


def relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def record_link(manifest_path: Path, line_number: int) -> str:
    rel_path = relative_path(manifest_path)
    label = f"{rel_path}:{line_number}"
    server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repository = os.getenv("GITHUB_REPOSITORY")
    sha = os.getenv("GITHUB_SHA")

    if repository and sha:
        url = f"{server_url}/{repository}/blob/{sha}/{rel_path}#L{line_number}"
        return f"[{label}]({url})"
    return label


def build_summary(
    manifest_path: Path,
    manifest_entries: Sequence[Mapping[str, Any]],
) -> str:
    active_entries = [
        entry for entry in manifest_entries
        if "expires_on_date" in entry
    ]
    if not active_entries:
        return "No active dependency review exceptions are tracked.\n"

    lines = [
        "## Dependency Review Exceptions",
        "",
        "| Dependency | Advisory | Owner | Expires | Record |",
        "| --- | --- | --- | --- | --- |",
    ]
    line_numbers = record_line_numbers(manifest_path, active_entries)
    for entry in active_entries:
        entry_id = str(entry.get("id", ""))
        line_number = line_numbers.get(entry_id, 1)
        dependency = f"{entry.get('ecosystem')}:{entry.get('package')}"
        advisory = str(entry.get("advisory") or "-")
        lines.append(
            "| {dependency} | {advisory} | {owner} | {expires} | {record} |"
            .format(
                dependency=dependency,
                advisory=advisory,
                owner=entry.get("owner"),
                expires=entry.get("expires_on"),
                record=record_link(manifest_path, line_number),
            )
        )
    return "\n".join(lines) + "\n"


def load_overrides_from_env() -> Optional[Any]:
    raw = os.getenv("DEPENDENCY_REVIEW_OVERRIDES")
    if not raw:
        return None
    return json.loads(raw)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate dependency review exception records.",
    )
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--overrides")
    parser.add_argument("--summary")
    parser.add_argument("--today")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    manifest_path = Path(args.manifest)

    try:
        today = (
            dt.date.fromisoformat(args.today)
            if args.today else dt.date.today()
        )
        manifest_data = load_json(manifest_path)
        manifest_entries, errors = validate_manifest(manifest_data, today)

        if args.overrides:
            overrides_data = load_json(Path(args.overrides))
        else:
            overrides_data = load_overrides_from_env()
        if overrides_data is not None:
            errors.extend(validate_overrides(overrides_data, manifest_entries))

        summary = build_summary(manifest_path, manifest_entries)
        if args.summary:
            Path(args.summary).write_text(summary, encoding="utf-8")
        elif os.getenv("GITHUB_STEP_SUMMARY"):
            Path(os.environ["GITHUB_STEP_SUMMARY"]).write_text(
                summary,
                encoding="utf-8",
            )
        else:
            print(summary, end="")

        if errors:
            for error in errors:
                print(f"dependency exception error: {error}", file=sys.stderr)
            return 1

        print(f"validated {len(manifest_entries)} exception records")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"dependency exception error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
