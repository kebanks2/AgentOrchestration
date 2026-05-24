"""Worker deployment resource request validation."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


class WorkerResourceValidationError(ValueError):
    """Raised when a worker manifest violates resource request policy."""


@dataclass(frozen=True)
class WorkerResourcePolicy:
    workload_class: str
    min_cpu_millicores: int
    min_memory_mib: int


DEFAULT_WORKER_RESOURCE_POLICIES: Dict[str, WorkerResourcePolicy] = {
    "background": WorkerResourcePolicy(
        workload_class="background",
        min_cpu_millicores=250,
        min_memory_mib=256,
    ),
    "standard": WorkerResourcePolicy(
        workload_class="standard",
        min_cpu_millicores=500,
        min_memory_mib=512,
    ),
    "high-memory": WorkerResourcePolicy(
        workload_class="high-memory",
        min_cpu_millicores=1000,
        min_memory_mib=2048,
    ),
}

_WORKER_KINDS = {
    "background-worker",
    "background_worker",
    "scheduler-worker",
    "scheduler_worker",
    "worker",
}

_MEMORY_UNITS = {
    "ki": 1 / 1024,
    "mi": 1,
    "gi": 1024,
    "ti": 1024 * 1024,
    "k": 1000 / (1024 * 1024),
    "m": 1000 * 1000 / (1024 * 1024),
    "g": 1000 * 1000 * 1000 / (1024 * 1024),
    "t": 1000 * 1000 * 1000 * 1000 / (1024 * 1024),
}


def load_manifest(path: str) -> Dict[str, Any]:
    try:
        with Path(path).open() as manifest_file:
            data = json.load(manifest_file)
    except json.JSONDecodeError as exc:
        raise WorkerResourceValidationError(
            f"Deployment manifest is not valid JSON: {exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise WorkerResourceValidationError(
            "Deployment manifest must be an object"
        )
    return data


def validate_worker_resource_requests(
    manifest: Mapping[str, Any],
    policies: Mapping[
        str,
        WorkerResourcePolicy,
    ] = DEFAULT_WORKER_RESOURCE_POLICIES,
) -> Optional[Dict[str, Any]]:
    """Validate resource requests for worker manifests.

    Non-worker manifests return ``None`` so shared deploy paths can validate
    only the manifests that carry worker workload semantics.
    """
    if not _is_worker_manifest(manifest):
        return None

    workload_class = _workload_class(manifest)
    policy = policies.get(workload_class)
    if policy is None:
        allowed = ", ".join(sorted(policies))
        raise WorkerResourceValidationError(
            f"Unknown worker workload_class '{workload_class}' "
            f"(allowed: {allowed})"
        )

    requests = _resource_requests(manifest)
    if "cpu" not in requests:
        raise WorkerResourceValidationError("Worker cpu request is required")
    if "memory" not in requests:
        raise WorkerResourceValidationError(
            "Worker memory request is required"
        )

    cpu_millicores = _parse_cpu_millicores(requests["cpu"])
    memory_mib = _parse_memory_mib(requests["memory"])
    if cpu_millicores < policy.min_cpu_millicores:
        raise WorkerResourceValidationError(
            f"Worker cpu request {cpu_millicores}m is below "
            f"{policy.min_cpu_millicores}m for {workload_class}"
        )
    if memory_mib < policy.min_memory_mib:
        raise WorkerResourceValidationError(
            f"Worker memory request {memory_mib}Mi is below "
            f"{policy.min_memory_mib}Mi for {workload_class}"
        )

    return {
        "workload_class": workload_class,
        "cpu_millicores": cpu_millicores,
        "memory_mib": memory_mib,
        "min_cpu_millicores": policy.min_cpu_millicores,
        "min_memory_mib": policy.min_memory_mib,
    }


def _is_worker_manifest(manifest: Mapping[str, Any]) -> bool:
    spec = _mapping(manifest.get("spec"))
    labels = _mapping(_mapping(manifest.get("metadata")).get("labels"))
    candidates = [
        manifest.get("kind"),
        manifest.get("type"),
        manifest.get("component"),
        spec.get("kind"),
        spec.get("type"),
        spec.get("component"),
        spec.get("workload_class"),
        spec.get("workloadClass"),
        labels.get("workload-class"),
        labels.get("worker-class"),
    ]
    if any(_normalize(candidate) in _WORKER_KINDS for candidate in candidates):
        return True
    return any(
        _normalize(_mapping(container).get("name")) in _WORKER_KINDS
        for container in _containers(manifest)
    )


def _workload_class(manifest: Mapping[str, Any]) -> str:
    spec = _mapping(manifest.get("spec"))
    labels = _mapping(_mapping(manifest.get("metadata")).get("labels"))
    value = (
        spec.get("workload_class")
        or spec.get("workloadClass")
        or labels.get("workload-class")
        or labels.get("worker-class")
        or "background"
    )
    if not isinstance(value, str) or not value.strip():
        raise WorkerResourceValidationError(
            "Worker workload_class is required"
        )
    return value.strip().lower().replace("_", "-")


def _resource_requests(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    spec = _mapping(manifest.get("spec"))
    direct_requests = _mapping(_mapping(spec.get("resources")).get("requests"))
    if direct_requests:
        return direct_requests

    for container in _containers(manifest):
        container_requests = _mapping(
            _mapping(_mapping(container).get("resources")).get("requests")
        )
        if container_requests:
            return container_requests
    raise WorkerResourceValidationError(
        "Worker resource requests are required"
    )


def _parse_cpu_millicores(value: Any) -> int:
    if isinstance(value, bool):
        raise WorkerResourceValidationError(
            "Worker cpu request must be numeric"
        )
    if isinstance(value, (int, float)):
        if value <= 0:
            raise WorkerResourceValidationError(
                "Worker cpu request must be positive"
            )
        return int(round(float(value) * 1000))
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            raise WorkerResourceValidationError(
                "Worker cpu request must be set"
            )
        if normalized.endswith("m"):
            return _positive_int(normalized[:-1], "Worker cpu request")
        cores = _positive_float(normalized, "Worker cpu request")
        return int(round(cores * 1000))
    raise WorkerResourceValidationError("Worker cpu request must be numeric")


def _parse_memory_mib(value: Any) -> int:
    if isinstance(value, bool):
        raise WorkerResourceValidationError(
            "Worker memory request must be numeric"
        )
    if isinstance(value, (int, float)):
        if value <= 0:
            raise WorkerResourceValidationError(
                "Worker memory request must be positive"
            )
        return int(round(float(value)))
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            raise WorkerResourceValidationError(
                "Worker memory request must be set"
            )
        for unit, multiplier in _MEMORY_UNITS.items():
            if normalized.endswith(unit):
                amount = _positive_float(
                    normalized[: -len(unit)],
                    "Worker memory request",
                )
                return int(round(amount * multiplier))
        return _positive_int(normalized, "Worker memory request")
    raise WorkerResourceValidationError(
        "Worker memory request must be numeric"
    )


def _positive_int(value: str, field_name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise WorkerResourceValidationError(
            f"{field_name} is invalid"
        ) from exc
    if parsed <= 0:
        raise WorkerResourceValidationError(f"{field_name} must be positive")
    return parsed


def _positive_float(value: str, field_name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise WorkerResourceValidationError(
            f"{field_name} is invalid"
        ) from exc
    if parsed <= 0:
        raise WorkerResourceValidationError(f"{field_name} must be positive")
    return parsed


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _containers(manifest: Mapping[str, Any]) -> list:
    spec = _mapping(manifest.get("spec"))
    template_spec = _mapping(_mapping(spec.get("template")).get("spec"))
    containers = (
        template_spec.get("containers")
        or spec.get("containers")
        or []
    )
    return containers if isinstance(containers, list) else []


def _normalize(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()
