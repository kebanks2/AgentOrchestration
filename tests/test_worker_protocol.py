import asyncio

import pytest

from src.orchestrator.scheduler import (
    TaskScheduler,
    WorkerProtocolError,
    WorkerProtocolSettings,
)


def test_worker_protocol_requires_ack_timeout_to_exceed_visibility():
    with pytest.raises(
        WorkerProtocolError,
        match="ack_timeout must exceed visibility_timeout",
    ):
        WorkerProtocolSettings(ack_timeout=10, visibility_timeout=10)

    with pytest.raises(
        WorkerProtocolError,
        match="ack_timeout must exceed visibility_timeout",
    ):
        WorkerProtocolSettings(ack_timeout=5, visibility_timeout=10)


def test_enqueue_rejects_invalid_task_protocol_before_queue_mutation():
    scheduler = TaskScheduler()

    with pytest.raises(
        WorkerProtocolError,
        match="ack_timeout must exceed visibility_timeout",
    ):
        scheduler.enqueue(
            {
                "type": "unsafe",
                "ack_timeout": 5,
                "visibility_timeout": 10,
            }
        )

    assert asyncio.run(scheduler.dequeue()) is None


def test_enqueue_attaches_validated_protocol_settings_to_task():
    scheduler = TaskScheduler(
        worker_protocol=WorkerProtocolSettings(
            ack_timeout=45,
            visibility_timeout=15,
        )
    )

    scheduler.enqueue({"type": "safe"})
    task = asyncio.run(scheduler.dequeue())

    assert task["ack_timeout"] == 45
    assert task["visibility_timeout"] == 15


def test_retry_is_idempotent_and_preserves_task_identity():
    scheduler = TaskScheduler()
    original_id = scheduler.enqueue({"type": "retry-me"})
    task = asyncio.run(scheduler.dequeue())

    assert scheduler.fail(task["id"]) is True
    retried = asyncio.run(scheduler.dequeue())

    assert retried["id"] == original_id
    assert retried["retries"] == 1
    assert retried["ack_timeout"] > retried["visibility_timeout"]


def test_retry_rejects_protocol_drift_without_payload_audit():
    scheduler = TaskScheduler()
    scheduler.enqueue({"type": "retry-me", "payload": {"secret": "raw"}})
    task = asyncio.run(scheduler.dequeue())
    task["visibility_timeout"] = task["ack_timeout"]

    assert scheduler.fail(task["id"]) is False

    assert scheduler.audit_records() == [
        {
            "event": "retry_rejected",
            "task_id": task["id"],
            "ack_timeout": task["ack_timeout"],
            "visibility_timeout": task["visibility_timeout"],
            "reason": "ack_timeout must exceed visibility_timeout",
        }
    ]
