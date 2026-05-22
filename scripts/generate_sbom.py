#!/usr/bin/env python3
"""Generate a strict release SBOM from project dependencies."""

import argparse
import json
import platform
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional


RequirementVersionProvider = Callable[[str], str]


class DependencyResolutionError(RuntimeError):
    def __init__(self, unresolved: Iterable[str]):
        self.unresolved = sorted(set(unresolved))
        message = (
            "Unresolved dependencies in release SBOM: "
            + ", ".join(self.unresolved)
        )
        super().__init__(message)


def dependency_names(pyproject_path: Path) -> List[str]:
    config = _load_pyproject(pyproject_path)
    dependencies = list(config.get("project", {}).get("dependencies", []))
    dependencies.extend(
        config.get("tool", {}).get("uv", {}).get("dev-dependencies", [])
    )
    names = []
    for requirement in dependencies:
        match = re.match(r"\s*([A-Za-z0-9_.-]+)", requirement)
        if match:
            names.append(match.group(1).replace("_", "-").lower())
    return sorted(set(names))


def build_sbom(
    pyproject_path: Path,
    *,
    strict: bool = True,
    target_platform: Optional[str] = None,
    package_manager_version: Optional[str] = None,
    version_provider: RequirementVersionProvider = metadata.version,
) -> Dict:
    target_platform = target_platform or platform.platform()
    package_manager_version = (
        package_manager_version or detect_package_manager_version()
    )
    components = []
    unresolved = []

    for name in dependency_names(pyproject_path):
        try:
            version = version_provider(name)
        except metadata.PackageNotFoundError:
            unresolved.append(name)
            if strict:
                continue
            version = "unresolved"

        component = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:pypi/{name}@{version}",
            "properties": [
                {
                    "name": "agent-orchestrator:dependency-resolution",
                    "value": "resolved"
                    if name not in unresolved
                    else "unresolved",
                }
            ],
        }
        components.append(component)

    if strict and unresolved:
        raise DependencyResolutionError(unresolved)

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "agent-orchestrator",
            },
            "properties": [
                {
                    "name": "agent-orchestrator:target-platform",
                    "value": target_platform,
                },
                {
                    "name": "agent-orchestrator:package-manager-version",
                    "value": package_manager_version,
                },
                {
                    "name": "agent-orchestrator:strict-resolution",
                    "value": str(strict).lower(),
                },
            ],
        },
        "components": components,
    }


def detect_package_manager_version() -> str:
    commands = (
        ["uv", "--version"],
        [sys.executable, "-m", "pip", "--version"],
    )
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            continue
        return completed.stdout.strip()
    return "unknown"


def _load_pyproject(pyproject_path: Path) -> Dict:
    text = pyproject_path.read_text(encoding="utf-8")
    try:
        import tomllib

        return tomllib.loads(text)
    except ModuleNotFoundError:
        return _parse_dependency_sections(text)


def _parse_dependency_sections(text: str) -> Dict:
    sections: Dict[str, List[str]] = {"project.dependencies": []}
    sections["tool.uv.dev-dependencies"] = []
    current = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == "dependencies = [":
            current = "project.dependencies"
            continue
        if line == "dev-dependencies = [":
            current = "tool.uv.dev-dependencies"
            continue
        if current and line == "]":
            current = None
            continue
        if not current or not line or line.startswith("#"):
            continue
        value = line.rstrip(",").strip().strip('"').strip("'")
        if value:
            sections[current].append(value)

    return {
        "project": {"dependencies": sections["project.dependencies"]},
        "tool": {
            "uv": {
                "dev-dependencies": sections["tool.uv.dev-dependencies"]
            }
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pyproject", default="pyproject.toml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--strict", action="store_true", default=False)
    parser.add_argument("--allow-unresolved", action="store_true")
    parser.add_argument("--target-platform")
    parser.add_argument("--package-manager-version")
    args = parser.parse_args(argv)

    strict = args.strict or not args.allow_unresolved
    try:
        sbom = build_sbom(
            Path(args.pyproject),
            strict=strict,
            target_platform=args.target_platform,
            package_manager_version=args.package_manager_version,
        )
    except DependencyResolutionError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    Path(args.output).write_text(
        json.dumps(sbom, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
