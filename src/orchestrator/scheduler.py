"""Task Scheduler — Priority-based task queuing and dispatch."""

import heapq
import time
from typing import Any, Dict, List, Optional
from uuid import uuid4

from src.common.metrics import metrics


class PriorityQueue:
    def __init__(self):
        self._queue = []
        self._counter = 0

    def push(self, item: Any, priority: int = 0) -> None:
        heapq.heappush(self._queue, (-priority, self._counter, item))
        self._counter += 1

    def pop(self) -> Optional[Any]:
        if self._queue:
            return heapq.heappop(self._queue)[2]
        return None

    def peek(self) -> Optional[Any]:
        if self._queue:
            return self._queue[0][2]
        return None

    def __len__(self) -> int:
        return len(self._queue)


class TaskScheduler:
    def __init__(self):
        self._queues: Dict[str, PriorityQueue] = {}
        self._scheduled: Dict[str, Dict[str, Any]] = {}
        self._in_flight: Dict[str, Dict] = {}
        self._audit_events: List[Dict[str, Any]] = []
        self._max_retries = 3

    def enqueue(
        self,
        task: Dict,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = task.get("id") or str(uuid4())
        task["id"] = task_id
        task["enqueued_at"] = time.time()
        task["priority"] = priority
        task["queue"] = queue
        task["status"] = "queued"
        task.pop("reserved_at", None)
        task.pop("reserved_by", None)
        task.pop("reservation_token", None)
        task.setdefault("retries", 0)

        if queue not in self._queues:
            self._queues[queue] = PriorityQueue()
        self._queues[queue].push(task, priority)
        return task_id

    def schedule(
        self,
        task: Dict,
        delay: float,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = task.get("id") or str(uuid4())
        task["id"] = task_id
        task["queue"] = queue
        task["priority"] = priority
        task["status"] = "scheduled"
        task.setdefault("retries", 0)
        self._scheduled[task_id] = {
            "due_at": time.time() + delay,
            "priority": priority,
            "queue": queue,
            "task": task,
        }
        return task_id

    async def dequeue(
        self,
        queue: str = "default",
        timeout: float = 1.0,
        worker_id: Optional[str] = None,
    ) -> Optional[Dict]:
        now = time.time()
        expired = [
            tid
            for tid, entry in self._scheduled.items()
            if entry["queue"] == queue and entry["due_at"] <= now
        ]
        for tid in expired:
            entry = self._scheduled.pop(tid)
            self.enqueue(
                entry["task"],
                entry["queue"],
                priority=entry["priority"],
            )

        if queue in self._queues and len(self._queues[queue]) > 0:
            task = self._queues[queue].pop()
            if task:
                task["status"] = "reserved"
                task["reserved_at"] = now
                task["reserved_by"] = worker_id
                task["reservation_token"] = str(uuid4())
                self._in_flight[task["id"]] = task
                self._record_audit(
                    "reservation_created",
                    task["id"],
                    queue=queue,
                    worker_id=worker_id,
                )
                return task
        return None

    def complete(
        self,
        task_id: str,
        reservation_token: Optional[str] = None,
    ) -> bool:
        task = self._in_flight.get(task_id)
        if not task:
            return False
        if not self._matches_reservation(task, reservation_token):
            self._record_audit(
                "reservation_rejected",
                task_id,
                queue=task.get("queue", "default"),
                worker_id=task.get("reserved_by"),
                reason="stale_reservation_token",
            )
            metrics.increment("scheduler.stale_reservations_rejected")
            return False

        self._in_flight.pop(task_id)
        task["status"] = "completed"
        self._record_audit(
            "reservation_completed",
            task_id,
            queue=task.get("queue", "default"),
            worker_id=task.get("reserved_by"),
        )
        return True

    def fail(
        self,
        task_id: str,
        queue: str = "default",
        reservation_token: Optional[str] = None,
    ) -> bool:
        task = self._in_flight.get(task_id)
        if task:
            worker_id = task.get("reserved_by")
            if not self._matches_reservation(task, reservation_token):
                self._record_audit(
                    "reservation_rejected",
                    task_id,
                    queue=queue,
                    worker_id=worker_id,
                    reason="stale_reservation_token",
                )
                metrics.increment("scheduler.stale_reservations_rejected")
                return False

            self._in_flight.pop(task_id)
            task["retries"] += 1
            if task["retries"] < self._max_retries:
                self.enqueue(task, queue, priority=task.get("priority", 0))
                self._record_audit(
                    "reservation_retried",
                    task_id,
                    queue=queue,
                    worker_id=worker_id,
                )
                return True
            task["status"] = "failed"
            self._record_audit(
                "reservation_failed",
                task_id,
                queue=queue,
                worker_id=worker_id,
            )
        return False

    def worker_disconnected(
        self,
        worker_id: str,
        queue: str = "default",
    ) -> List[str]:
        reclaimed = []
        for task_id, task in list(self._in_flight.items()):
            if task.get("reserved_by") != worker_id:
                continue
            self._in_flight.pop(task_id)
            self.enqueue(
                task,
                task.get("queue", queue),
                priority=task.get("priority", 0),
            )
            reclaimed.append(task_id)
            self._record_audit(
                "reservation_reclaimed",
                task_id,
                queue=task.get("queue", queue),
                worker_id=worker_id,
                reason="worker_disconnected",
            )

        if reclaimed:
            metrics.increment(
                "scheduler.reservations_reclaimed",
                len(reclaimed),
            )
        return reclaimed

    def reclaim_abandoned(
        self,
        reservation_timeout: float,
        queue: str = "default",
        now: Optional[float] = None,
    ) -> List[str]:
        cutoff = (time.time() if now is None else now) - reservation_timeout
        reclaimed = []
        for task_id, task in list(self._in_flight.items()):
            reserved_at = task.get("reserved_at")
            if reserved_at is None or reserved_at > cutoff:
                continue
            worker_id = task.get("reserved_by")
            self._in_flight.pop(task_id)
            self.enqueue(
                task,
                task.get("queue", queue),
                priority=task.get("priority", 0),
            )
            reclaimed.append(task_id)
            self._record_audit(
                "reservation_reclaimed",
                task_id,
                queue=task.get("queue", queue),
                worker_id=worker_id,
                reason="reservation_timeout",
            )

        if reclaimed:
            metrics.increment(
                "scheduler.reservations_reclaimed",
                len(reclaimed),
            )
        return reclaimed

    @property
    def audit_events(self) -> List[Dict[str, Any]]:
        return list(self._audit_events)

    def _record_audit(
        self,
        event: str,
        task_id: str,
        queue: str = "default",
        worker_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        record = {
            "event": event,
            "task_id": task_id,
            "queue": queue,
            "worker_id": worker_id,
            "timestamp": time.time(),
        }
        if reason:
            record["reason"] = reason
        self._audit_events.append(record)

    def _matches_reservation(
        self,
        task: Dict[str, Any],
        reservation_token: Optional[str],
    ) -> bool:
        if reservation_token is None:
            return True
        return task.get("reservation_token") == reservation_token

# 2019-04-25T08:37:12 update

# 2019-06-04T16:40:00 update

# 2019-07-11T12:01:28 update

# 2019-08-02T12:20:21 update

# 2019-08-23T10:38:50 update

# 2019-10-31T13:55:52 update

# 2019-11-04T20:12:32 update

# 2019-12-13T12:22:36 update

# 2020-02-01T10:32:37 update

# 2020-02-26T09:44:38 update

# 2020-03-09T19:00:55 update

# 2020-05-01T18:40:34 update

# 2020-05-12T15:10:31 update

# 2020-06-30T13:24:19 update

# 2020-09-22T16:00:45 update

# 2020-10-20T10:52:48 update

# 2020-10-21T12:18:08 update

# 2020-11-06T12:35:01 update

# 2020-12-09T08:09:33 update

# 2021-01-07T08:20:36 update

# 2021-10-02T15:23:16 update

# 2021-10-06T16:14:57 update

# 2021-10-06T09:27:41 update

# 2021-11-19T08:37:40 update

# 2022-03-01T16:39:54 update

# 2022-05-26T13:43:07 update

# 2022-06-02T10:50:58 update

# 2022-06-14T10:46:48 update

# 2022-07-31T16:44:34 update

# 2022-08-30T18:20:12 update

# 2022-11-04T14:47:03 update

# 2022-12-06T10:36:49 update

# 2022-12-22T13:21:12 update

# 2022-12-26T12:24:50 update

# 2023-03-09T08:09:55 update

# 2023-05-01T10:07:37 update

# 2023-06-08T14:32:15 update

# 2023-07-14T17:24:18 update

# 2023-12-14T08:38:31 update

# 2024-02-20T13:43:58 update

# 2024-03-24T08:52:42 update

# 2024-03-28T15:27:17 update

# 2024-03-29T18:10:33 update

# 2024-04-15T20:18:31 update

# 2024-05-27T13:11:52 update

# 2024-05-27T16:42:56 update

# 2024-06-20T13:03:45 update

# 2024-06-28T12:32:58 update

# 2024-07-10T14:10:16 update

# 2024-07-26T14:18:59 update

# 2024-08-12T08:21:05 update

# 2024-08-21T16:58:40 update

# 2024-09-27T19:54:30 update

# 2024-10-21T13:47:42 update

# 2024-11-11T09:19:27 update

# 2024-12-24T08:23:41 update

# 2025-02-14T10:35:15 update

# 2025-03-31T18:09:40 update

# 2025-06-21T17:32:49 update

# 2025-07-21T16:52:28 update

# 2025-08-20T19:45:16 update

# 2025-11-04T18:54:24 update

# 2025-12-09T20:17:36 update

# 2026-01-12T15:42:32 update

# 2026-01-23T14:41:20 update

# 2026-03-18T14:43:07 update

# 2026-04-13T11:43:19 update
