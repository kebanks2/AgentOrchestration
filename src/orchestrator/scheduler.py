"""Task Scheduler — Priority-based task queuing and dispatch."""

import heapq
import logging
import time
from typing import Any, Callable, Dict, Iterable, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)
HealthCheck = Callable[[], bool]


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
    def __init__(
        self,
        dependency_defer_delay: float = 30.0,
        max_audit_records: int = 100,
    ):
        self._queues: Dict[str, PriorityQueue] = {}
        self._scheduled: Dict[str, Dict] = {}
        self._in_flight: Dict[str, Dict] = {}
        self._dependency_health: Dict[str, bool] = {}
        self._dependency_checks: Dict[str, HealthCheck] = {}
        self._audit_records: List[Dict[str, Any]] = []
        self._dependency_defer_delay = dependency_defer_delay
        self._max_audit_records = max_audit_records
        self._max_retries = 3

    def enqueue(
        self,
        task: Dict,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = str(uuid4())
        task["id"] = task_id
        task["enqueued_at"] = time.time()
        task["queue"] = queue
        task["priority"] = priority
        task.setdefault("retries", 0)
        task.setdefault("status", "pending")
        task.setdefault("dependency_deferrals", 0)

        self._push_task(task, queue, priority)
        return task_id

    def _push_task(self, task: Dict, queue: str, priority: int) -> None:
        if queue not in self._queues:
            self._queues[queue] = PriorityQueue()
        self._queues[queue].push(task, priority)

    def schedule(
        self,
        task: Dict,
        delay: float,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = str(uuid4())
        task["id"] = task_id
        task["queue"] = queue
        task["priority"] = priority
        task.setdefault("retries", 0)
        task.setdefault("status", "pending")
        task.setdefault("dependency_deferrals", 0)
        self._schedule_existing(task, delay, queue, priority)
        return task_id

    def register_dependency(self, name: str, check: HealthCheck) -> None:
        self._dependency_checks[name] = check

    def set_dependency_health(self, name: str, healthy: bool) -> None:
        self._dependency_health[name] = bool(healthy)

    @property
    def audit_records(self) -> List[Dict[str, Any]]:
        return list(self._audit_records)

    async def dequeue(
        self,
        queue: str = "default",
        timeout: float = 1.0,
    ) -> Optional[Dict]:
        self._release_due_scheduled()

        while queue in self._queues and len(self._queues[queue]) > 0:
            task = self._queues[queue].pop()
            if not task:
                continue

            unhealthy = self._unhealthy_dependencies(task)
            if unhealthy:
                self._defer_for_dependencies(task, queue, unhealthy)
                continue

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
                self._push_task(task, queue, priority=task.get("priority", 0))
                return True
        return False

    def _release_due_scheduled(self) -> None:
        now = time.time()
        expired = [
            tid
            for tid, task in self._scheduled.items()
            if task["scheduled_for"] <= now
        ]
        for tid in expired:
            task = self._scheduled.pop(tid)
            task["enqueued_at"] = now
            task.pop("scheduled_for", None)
            if task.get("status") == "deferred":
                task["status"] = "pending"
            self._push_task(
                task,
                task.get("queue", "default"),
                task.get("priority", 0),
            )

    def _schedule_existing(
        self,
        task: Dict,
        delay: float,
        queue: str,
        priority: int,
    ) -> None:
        task["queue"] = queue
        task["priority"] = priority
        task["scheduled_for"] = time.time() + delay
        self._scheduled[task["id"]] = task

    def _unhealthy_dependencies(self, task: Dict) -> List[str]:
        dependencies = self._task_dependencies(task)
        return [
            name
            for name in dependencies
            if not self._dependency_is_healthy(name)
        ]

    def _task_dependencies(self, task: Dict) -> List[str]:
        raw = (
            task.get("external_dependencies")
            or task.get("dependencies")
            or task.get("required_dependencies")
            or []
        )
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, Iterable):
            return [str(name) for name in raw if str(name).strip()]
        return []

    def _dependency_is_healthy(self, name: str) -> bool:
        if name in self._dependency_checks:
            try:
                return bool(self._dependency_checks[name]())
            except Exception:
                logger.warning(
                    "scheduler dependency health check failed",
                    extra={"dependency": name},
                )
                return False
        return self._dependency_health.get(name, True)

    def _defer_for_dependencies(
        self,
        task: Dict,
        queue: str,
        unhealthy: List[str],
    ) -> None:
        task["status"] = "deferred"
        task["dependency_deferrals"] = task.get("dependency_deferrals", 0) + 1
        task["deferred_at"] = time.time()
        task["defer_reason"] = "dependency_unhealthy"
        task["deferred_dependencies"] = list(unhealthy)
        self._schedule_existing(
            task,
            self._dependency_defer_delay,
            queue,
            task.get("priority", 0),
        )
        self._record_audit(
            {
                "decision": "deferred",
                "reason": "dependency_unhealthy",
                "task_id": task.get("id"),
                "task_type": task.get("type"),
                "queue": queue,
                "dependencies": list(unhealthy),
                "timestamp": task["deferred_at"],
            }
        )
        logger.info(
            "Deferred task because required dependencies are unhealthy",
            extra={
                "task_id": task.get("id"),
                "queue": queue,
                "dependencies": list(unhealthy),
            },
        )

    def _record_audit(self, record: Dict[str, Any]) -> None:
        self._audit_records.append(record)
        if len(self._audit_records) > self._max_audit_records:
            self._audit_records = self._audit_records[
                -self._max_audit_records:
            ]

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
