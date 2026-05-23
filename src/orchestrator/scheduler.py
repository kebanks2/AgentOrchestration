"""Task Scheduler — Priority-based task queuing and dispatch."""

import copy
import json
import logging
import heapq
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4


logger = logging.getLogger(__name__)


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


class SchedulerCoordinationStore:
    """Shared scheduler coordination state.

    This in-memory implementation mirrors the atomic API a database-backed
    store would expose in production: leader leases, idempotent cron records,
    and once-only due-job claims are all protected by one lock.
    """

    def __init__(self, clock: Optional[Callable[[], float]] = None):
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._leaders: Dict[str, Dict[str, Any]] = {}
        self._cron_jobs: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def claim_leadership(
        self,
        group: str,
        release_id: str,
        lease_ttl: float,
        now: Optional[float] = None,
    ) -> bool:
        now = self._clock() if now is None else now
        with self._lock:
            current = self._leaders.get(group)
            if current and current["expires_at"] > now:
                if current["release_id"] != release_id:
                    return False
                current["expires_at"] = now + lease_ttl
                return True

            previous_release_id = current["release_id"] if current else None
            self._leaders[group] = {
                "release_id": release_id,
                "expires_at": now + lease_ttl,
            }
            if previous_release_id != release_id:
                logger.info(
                    "scheduler leadership changed",
                    extra={
                        "scheduler_group": group,
                        "previous_release_id": previous_release_id,
                        "release_id": release_id,
                    },
                )
            return True

    def register_cron_job(
        self,
        group: str,
        job_key: str,
        task: Dict,
        run_at: float,
        queue: str,
        priority: int,
        release_id: str,
        cron_expression: str,
    ) -> Tuple[str, bool]:
        store_key = (group, job_key)
        with self._lock:
            existing = self._cron_jobs.get(store_key)
            if existing:
                return existing["task_id"], False

            task_id = task.get("id") or str(uuid4())
            stored_task = copy.deepcopy(task)
            stored_task["id"] = task_id
            stored_task["scheduled_by_release"] = release_id
            stored_task["cron_expression"] = cron_expression
            self._cron_jobs[store_key] = {
                "task_id": task_id,
                "task": stored_task,
                "run_at": run_at,
                "queue": queue,
                "priority": priority,
                "release_id": release_id,
                "cron_expression": cron_expression,
                "claimed_by": None,
                "claimed_at": None,
            }
            return task_id, True

    def claim_due_jobs(
        self,
        group: str,
        release_id: str,
        now: Optional[float] = None,
    ) -> List[Dict]:
        now = self._clock() if now is None else now
        claimed: List[Dict] = []
        with self._lock:
            for (job_group, job_key), record in self._cron_jobs.items():
                if job_group != group:
                    continue
                if record["claimed_by"] is not None or record["run_at"] > now:
                    continue
                record["claimed_by"] = release_id
                record["claimed_at"] = now
                claimed.append(
                    {
                        "job_key": job_key,
                        "task": copy.deepcopy(record["task"]),
                        "queue": record["queue"],
                        "priority": record["priority"],
                    }
                )
        return claimed

    def registered_job_count(self, group: Optional[str] = None) -> int:
        with self._lock:
            if group is None:
                return len(self._cron_jobs)
            return sum(
                1 for job_group, _ in self._cron_jobs if job_group == group
            )


class TaskScheduler:
    def __init__(
        self,
        release_id: Optional[str] = None,
        coordination_store: Optional[SchedulerCoordinationStore] = None,
        scheduler_group: str = "default",
        leader_lease_ttl: float = 30.0,
        clock: Optional[Callable[[], float]] = None,
    ):
        self._queues: Dict[str, PriorityQueue] = {}
        self._scheduled: Dict[str, Dict] = {}
        self._in_flight: Dict[str, Dict] = {}
        self._max_retries = 3
        self._clock = clock or time.time
        self.release_id = (
            release_id
            or os.getenv("RELEASE_ID")
            or os.getenv("GIT_SHA")
            or "local"
        )
        self.scheduler_group = scheduler_group
        self.leader_lease_ttl = leader_lease_ttl
        self.coordination_store = coordination_store

    def enqueue(
        self,
        task: Dict,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = task.get("id") or str(uuid4())
        task["id"] = task_id
        task["enqueued_at"] = self._clock()
        task["retries"] = task.get("retries", 0)
        task["priority"] = priority

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
        self._scheduled[task_id] = {
            "task": task,
            "run_at": self._clock() + delay,
            "queue": queue,
            "priority": priority,
        }
        return task_id

    def register_cron_job(
        self,
        task: Dict,
        cron_expression: str,
        delay: float = 0,
        queue: str = "default",
        priority: int = 0,
        job_key: Optional[str] = None,
    ) -> Optional[str]:
        if self.coordination_store is None:
            return self.schedule(task, delay, queue=queue, priority=priority)

        is_leader = self.coordination_store.claim_leadership(
            self.scheduler_group,
            self.release_id,
            self.leader_lease_ttl,
            now=self._clock(),
        )
        if not is_leader:
            logger.info(
                "scheduler cron registration skipped by non-leader",
                extra={
                    "scheduler_group": self.scheduler_group,
                    "release_id": self.release_id,
                    "cron_expression": cron_expression,
                },
            )
            return None

        effective_job_key = job_key or self._cron_job_key(
            task, cron_expression, queue, priority
        )
        task_id, created = self.coordination_store.register_cron_job(
            self.scheduler_group,
            effective_job_key,
            task,
            self._clock() + delay,
            queue,
            priority,
            self.release_id,
            cron_expression,
        )
        if not created:
            logger.info(
                "scheduler cron registration deduplicated",
                extra={
                    "scheduler_group": self.scheduler_group,
                    "release_id": self.release_id,
                    "job_key": effective_job_key,
                    "cron_expression": cron_expression,
                    "task_id": task_id,
                },
            )
        return task_id

    async def dequeue(
        self,
        queue: str = "default",
        timeout: float = 1.0,
    ) -> Optional[Dict]:
        now = self._clock()
        self._enqueue_due_coordinated_jobs(now)
        expired = [
            tid for tid, record in self._scheduled.items()
            if record["run_at"] <= now
        ]
        for tid in expired:
            record = self._scheduled.pop(tid)
            if record:
                self.enqueue(
                    record["task"],
                    record.get("queue", queue),
                    priority=record.get("priority", 0),
                )

        if queue in self._queues and len(self._queues[queue]) > 0:
            task = self._queues[queue].pop()
            if task:
                self._in_flight[task["id"]] = task
                return task
        return None

    def complete(self, task_id: str) -> bool:
        return self._in_flight.pop(task_id, None) is not None

    def fail(self, task_id: str, queue: str = "default") -> bool:
        task = self._in_flight.pop(task_id, None)
        if task:
            task["retries"] += 1
            if task["retries"] < self._max_retries:
                self.enqueue(task, queue, priority=task.get("priority", 0))
                return True
        return False

    def _enqueue_due_coordinated_jobs(self, now: float) -> None:
        if self.coordination_store is None:
            return
        due_jobs = self.coordination_store.claim_due_jobs(
            self.scheduler_group,
            self.release_id,
            now=now,
        )
        for record in due_jobs:
            self.enqueue(
                record["task"],
                record["queue"],
                priority=record["priority"],
            )

    def _cron_job_key(
        self,
        task: Dict,
        cron_expression: str,
        queue: str,
        priority: int,
    ) -> str:
        payload = {
            "type": task.get("type"),
            "payload": task.get("payload", {}),
            "queue": queue,
            "priority": priority,
            "cron_expression": cron_expression,
            "job_key": task.get("job_key"),
        }
        return json.dumps(payload, sort_keys=True, default=str)

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
