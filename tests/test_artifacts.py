import pytest

from src.common.artifacts import ArtifactStore, SuspiciousArtifactMetadataError


def test_same_name_different_content_creates_distinct_blobs():
    store = ArtifactStore()

    first = store.put("report.json", b'{"status":"queued"}')
    second = store.put("report.json", b'{"status":"done"}')

    assert first.content_digest != second.content_digest
    assert first.blob_ref != second.blob_ref
    assert store.blob_count == 2
    assert store.artifact_count == 2


def test_same_content_deduplicates_by_verified_digest():
    store = ArtifactStore()

    first = store.put("task-a/output.txt", b"same bytes", {"task": "a"})
    second = store.put("task-b/output.txt", b"same bytes", {"task": "b"})

    assert first.content_digest == second.content_digest
    assert first.blob_ref == second.blob_ref
    assert store.blob_count == 1
    assert store.artifact_count == 2


def test_digest_metadata_mismatch_rejected_without_creating_blob():
    store = ArtifactStore()
    trusted = store.put("result.bin", b"trusted-content")

    with pytest.raises(SuspiciousArtifactMetadataError):
        store.put(
            "result.bin",
            b"different-content",
            {"content_digest": trusted.content_digest},
        )

    assert store.blob_count == 1


def test_blob_ref_metadata_mismatch_rejected_without_creating_blob():
    store = ArtifactStore()
    trusted = store.put("result.bin", b"trusted-content")

    with pytest.raises(SuspiciousArtifactMetadataError):
        store.put(
            "result.bin",
            b"different-content",
            {"blob_ref": trusted.blob_ref},
        )

    assert store.blob_count == 1


def test_blob_ref_reads_immutable_content_by_digest():
    store = ArtifactStore()
    record = store.put("result.bin", b"trusted-content")

    assert store.get_blob(record.blob_ref) == b"trusted-content"
