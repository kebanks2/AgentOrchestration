"""Idempotent checkpoint persistence for resumable workers."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional


class CheckpointConflictError(ValueError):
    """Raised when a checkpoint key is reused with different content."""


@dataclass(frozen=True, order=True)
class CheckpointKey:
    """Deterministic identity for one task step attempt checkpoint."""

    task_id: str
    step: str
    attempt: int

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("checkpoint task_id is required")
        if not self.step:
            raise ValueError("checkpoint step is required")
        if not isinstance(self.attempt, int) or self.attempt < 0:
            raise ValueError(
                "checkpoint attempt must be a non-negative integer"
            )

    @property
    def storage_key(self) -> str:
        raw_key = "\0".join((self.task_id, self.step, str(self.attempt)))
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CheckpointRecord:
    """Stored checkpoint record plus integrity metadata."""

    key: CheckpointKey
    payload: Dict[str, Any]
    digest: str
    created_at: float
    updated_at: float
    write_count: int = 1


class CheckpointStore:
    """Thread-safe checkpoint store with deterministic upsert semantics."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: Dict[str, CheckpointRecord] = {}
        self._task_index: Dict[str, set[str]] = {}

    def write(
        self,
        task_id: str,
        step: str,
        attempt: int,
        payload: Dict[str, Any],
    ) -> CheckpointRecord:
        """Write or confirm a checkpoint for a task step attempt.

        Retrying the same write returns a single logical record. Reusing that
        key with different content raises loudly so resume code cannot pick an
        ambiguous checkpoint.
        """

        key = CheckpointKey(task_id=task_id, step=step, attempt=attempt)
        digest = self._digest_payload(payload)
        storage_key = key.storage_key
        now = time.time()

        with self._lock:
            existing = self._records.get(storage_key)
            if existing:
                if existing.digest != digest:
                    raise CheckpointConflictError(
                        "checkpoint digest mismatch for "
                        f"task_id={task_id!r} step={step!r} "
                        f"attempt={attempt}: "
                        f"existing={existing.digest} new={digest}"
                    )
                updated = CheckpointRecord(
                    key=existing.key,
                    payload=existing.payload,
                    digest=existing.digest,
                    created_at=existing.created_at,
                    updated_at=now,
                    write_count=existing.write_count + 1,
                )
                self._records[storage_key] = updated
                return self._copy_record(updated)

            record = CheckpointRecord(
                key=key,
                payload=deepcopy(payload),
                digest=digest,
                created_at=now,
                updated_at=now,
            )
            self._records[storage_key] = record
            self._task_index.setdefault(task_id, set()).add(storage_key)
            return self._copy_record(record)

    def read(
        self,
        task_id: str,
        step: str,
        attempt: int,
    ) -> Optional[CheckpointRecord]:
        """Return a checkpoint record by deterministic key."""

        key = CheckpointKey(task_id=task_id, step=step, attempt=attempt)
        with self._lock:
            record = self._records.get(key.storage_key)
            return self._copy_record(record) if record else None

    def latest_for_task(self, task_id: str) -> Optional[CheckpointRecord]:
        """Return the latest checkpoint by attempt and update time."""

        with self._lock:
            records = list(self._iter_task_records(task_id))
        if not records:
            return None
        latest = max(
            records,
            key=lambda record: (record.key.attempt, record.updated_at),
        )
        return self._copy_record(latest)

    def count(self) -> int:
        with self._lock:
            return len(self._records)

    def _iter_task_records(self, task_id: str) -> Iterable[CheckpointRecord]:
        for storage_key in self._task_index.get(task_id, set()):
            record = self._records.get(storage_key)
            if record is not None:
                yield record

    @staticmethod
    def _digest_payload(payload: Dict[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _copy_record(record: CheckpointRecord) -> CheckpointRecord:
        return CheckpointRecord(
            key=record.key,
            payload=deepcopy(record.payload),
            digest=record.digest,
            created_at=record.created_at,
            updated_at=record.updated_at,
            write_count=record.write_count,
        )
