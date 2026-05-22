"""Deployment manifest rendering with immutable release identity metadata."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import NAMESPACE_URL, uuid5


ANNOTATION_PREFIX = "agent-orchestrator.io"
RELEASE_ID_ANNOTATION = f"{ANNOTATION_PREFIX}/release-id"
COMMIT_ANNOTATION = f"{ANNOTATION_PREFIX}/commit-sha"
PACKAGE_VERSION_ANNOTATION = f"{ANNOTATION_PREFIX}/package-version"
IMAGE_DIGEST_ANNOTATION = f"{ANNOTATION_PREFIX}/image-digest"
SOURCE_REF_ANNOTATION = f"{ANNOTATION_PREFIX}/source-ref"


@dataclass(frozen=True)
class ReleaseIdentity:
    commit_sha: str
    package_version: str
    image_digest: str
    source_ref: Optional[str] = None

    def __post_init__(self) -> None:
        for field_name in ("commit_sha", "package_version", "image_digest"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")

    @property
    def release_id(self) -> str:
        parts = (
            self.commit_sha.strip(),
            self.package_version.strip(),
            self.image_digest.strip(),
        )
        release_key = "agent-orchestrator:" + ":".join(parts)
        return str(uuid5(NAMESPACE_URL, release_key))

    def to_record(self, workload_name: str) -> Dict[str, Optional[str]]:
        return {
            "release_id": self.release_id,
            "workload": workload_name,
            "commit_sha": self.commit_sha,
            "package_version": self.package_version,
            "image_digest": self.image_digest,
            "source_ref": self.source_ref,
        }


class DeploymentHistory:
    def __init__(self):
        self._records: Dict[str, List[Dict[str, Optional[str]]]] = {}

    def record(
        self,
        workload_name: str,
        release_identity: ReleaseIdentity,
    ) -> Dict[str, Optional[str]]:
        record = release_identity.to_record(workload_name)
        release_records = self._records.setdefault(
            release_identity.release_id,
            [],
        )
        release_records.append(record)
        return record

    def find_by_release_id(
        self,
        release_id: str,
    ) -> List[Dict[str, Optional[str]]]:
        return list(self._records.get(release_id, []))


class DeploymentManifestRenderer:
    def __init__(self, history: Optional[DeploymentHistory] = None):
        self.history = history or DeploymentHistory()

    def render(
        self,
        manifest: Dict[str, Any],
        release_identity: ReleaseIdentity,
    ) -> Dict[str, Any]:
        workload = deepcopy(manifest)
        metadata = workload.setdefault("metadata", {})
        annotations = metadata.setdefault("annotations", {})
        labels = metadata.setdefault("labels", {})

        annotations[RELEASE_ID_ANNOTATION] = release_identity.release_id
        annotations[COMMIT_ANNOTATION] = release_identity.commit_sha
        annotations[PACKAGE_VERSION_ANNOTATION] = (
            release_identity.package_version
        )
        annotations[IMAGE_DIGEST_ANNOTATION] = release_identity.image_digest
        if release_identity.source_ref:
            annotations[SOURCE_REF_ANNOTATION] = release_identity.source_ref

        labels[RELEASE_ID_ANNOTATION] = release_identity.release_id
        workload_name = metadata.get("name", "unnamed-workload")
        release_record = self.history.record(workload_name, release_identity)

        return {
            "workload": workload,
            "release": release_record,
        }
