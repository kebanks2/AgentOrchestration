"""Content-addressed artifact storage helpers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


class SuspiciousArtifactMetadataError(ValueError):
    """Raised when caller metadata disagrees with verified artifact content."""


@dataclass(frozen=True)
class BlobRecord:
    blob_ref: str
    content_digest: str
    size: int
    content: bytes


@dataclass(frozen=True)
class ArtifactRecord:
    logical_name: str
    blob_ref: str
    content_digest: str
    size: int
    metadata: Dict[str, Any]


class ArtifactStore:
    """In-memory content-addressed artifact store.

    The store computes the content digest before any deduplication decision,
    then uses that verified digest as the immutable blob identity. Logical
    names and caller metadata are descriptive only and cannot redirect an
    upload to a different blob.
    """

    def __init__(self) -> None:
        self._blobs: Dict[str, BlobRecord] = {}
        self._artifacts: Dict[str, ArtifactRecord] = {}

    def put(
        self,
        logical_name: str,
        content: bytes,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> ArtifactRecord:
        if not isinstance(logical_name, str) or not logical_name.strip():
            raise ValueError("logical_name must be a non-empty string")
        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")

        metadata_copy = dict(metadata or {})
        content_digest = hashlib.sha256(content).hexdigest()
        blob_ref = f"sha256:{content_digest}"

        self._validate_metadata(metadata_copy, content_digest, blob_ref)

        blob = self._blobs.get(content_digest)
        if blob is None:
            blob = BlobRecord(
                blob_ref=blob_ref,
                content_digest=content_digest,
                size=len(content),
                content=bytes(content),
            )
            self._blobs[content_digest] = blob

        artifact_key = f"{logical_name}:{content_digest}"
        record = ArtifactRecord(
            logical_name=logical_name,
            blob_ref=blob.blob_ref,
            content_digest=blob.content_digest,
            size=blob.size,
            metadata={
                **metadata_copy,
                "content_digest": blob.content_digest,
                "blob_ref": blob.blob_ref,
            },
        )
        self._artifacts[artifact_key] = record
        return record

    def get_blob(self, blob_ref: str) -> bytes:
        if not blob_ref.startswith("sha256:"):
            raise KeyError(blob_ref)
        digest = blob_ref.removeprefix("sha256:")
        return self._blobs[digest].content

    @property
    def blob_count(self) -> int:
        return len(self._blobs)

    @property
    def artifact_count(self) -> int:
        return len(self._artifacts)

    def _validate_metadata(
        self,
        metadata: Mapping[str, Any],
        content_digest: str,
        blob_ref: str,
    ) -> None:
        claimed_digest = metadata.get("content_digest")
        if claimed_digest is not None and claimed_digest != content_digest:
            raise SuspiciousArtifactMetadataError(
                "metadata content_digest does not match artifact content"
            )

        claimed_blob_ref = metadata.get("blob_ref")
        if claimed_blob_ref is not None and claimed_blob_ref != blob_ref:
            raise SuspiciousArtifactMetadataError(
                "metadata blob_ref does not match artifact content"
            )
