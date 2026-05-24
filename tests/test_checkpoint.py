import pytest

from src.orchestrator.checkpoint import (
    CheckpointConflictError,
    CheckpointKey,
    CheckpointStore,
)


def test_checkpoint_key_is_deterministic_for_task_step_attempt():
    first = CheckpointKey("task-1", "extract", 2)
    second = CheckpointKey("task-1", "extract", 2)
    different_attempt = CheckpointKey("task-1", "extract", 3)

    assert first.storage_key == second.storage_key
    assert first.storage_key != different_attempt.storage_key


def test_retried_checkpoint_write_keeps_one_logical_record():
    store = CheckpointStore()
    payload = {"offset": 42, "state": {"phase": "uploading"}}

    first = store.write("task-1", "upload", 1, payload)
    retry = store.write(
        "task-1",
        "upload",
        1,
        {"state": {"phase": "uploading"}, "offset": 42},
    )

    assert store.count() == 1
    assert first.digest == retry.digest
    assert retry.write_count == 2
    assert store.read("task-1", "upload", 1).payload == payload


def test_digest_mismatch_for_same_checkpoint_key_fails_loudly():
    store = CheckpointStore()
    store.write("task-1", "upload", 1, {"offset": 42})

    with pytest.raises(
        CheckpointConflictError,
        match="checkpoint digest mismatch",
    ):
        store.write("task-1", "upload", 1, {"offset": 99})

    assert store.count() == 1
    assert store.read("task-1", "upload", 1).payload == {"offset": 42}


def test_timeout_retry_resume_uses_the_latest_attempt_without_duplicates():
    store = CheckpointStore()
    store.write("task-1", "upload", 1, {"offset": 10})

    # Simulate a worker timing out after persisting attempt 2, then retrying
    # the same checkpoint upload before resume logic asks for the latest state.
    store.write("task-1", "upload", 2, {"offset": 20})
    store.write("task-1", "upload", 2, {"offset": 20})

    latest = store.latest_for_task("task-1")

    assert store.count() == 2
    assert latest.key == CheckpointKey("task-1", "upload", 2)
    assert latest.payload == {"offset": 20}
    assert latest.write_count == 2


def test_checkpoint_payload_is_not_mutable_through_callers():
    store = CheckpointStore()
    payload = {"nested": {"offset": 10}}
    store.write("task-1", "upload", 1, payload)

    payload["nested"]["offset"] = 99
    read_record = store.read("task-1", "upload", 1)
    read_record.payload["nested"]["offset"] = 123

    assert store.read("task-1", "upload", 1).payload == {
        "nested": {"offset": 10}
    }


def test_checkpoint_keys_validate_required_identity_fields():
    with pytest.raises(ValueError, match="task_id"):
        CheckpointKey("", "upload", 1)
    with pytest.raises(ValueError, match="step"):
        CheckpointKey("task-1", "", 1)
    with pytest.raises(ValueError, match="attempt"):
        CheckpointKey("task-1", "upload", -1)
