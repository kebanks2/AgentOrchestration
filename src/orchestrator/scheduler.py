"""Task Scheduler — Priority-based task queuing and dispatch."""

from collections import deque
import heapq
import time
from typing import Any, Deque, Dict, List, Optional
from uuid import uuid4


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
    def __init__(self, audit_limit: int = 100):
        self._queues: Dict[str, PriorityQueue] = {}
        self._scheduled: Dict[str, Dict] = {}
        self._in_flight: Dict[str, Dict] = {}
        self._deferred: Dict[str, Dict] = {}
        self._dependency_health: Dict[str, Dict] = {}
        self._audit: Deque[Dict] = deque(maxlen=audit_limit)
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
        task["retries"] = 0
        task["priority"] = priority

        blocked = self._blocked_dependencies(task)
        if blocked:
            self._defer(task, queue, priority, blocked, "enqueue")
            return task_id

        self._push_ready(task, queue, priority)
        return task_id

    def schedule(
        self,
        task: Dict,
        delay: float,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = str(uuid4())
        task["id"] = task_id
        task["scheduled_at"] = time.time()
        task["retries"] = 0
        task["priority"] = priority
        self._scheduled[task_id] = {
            "task": task,
            "ready_at": time.time() + delay,
            "queue": queue,
            "priority": priority,
        }
        return task_id

    async def dequeue(
        self,
        queue: str = "default",
        timeout: float = 1.0,
    ) -> Optional[Dict]:
        self._promote_due_scheduled(queue)
        self._release_deferred()

        while queue in self._queues and len(self._queues[queue]) > 0:
            task = self._queues[queue].pop()
            if not task:
                continue

            blocked = self._blocked_dependencies(task)
            if blocked:
                self._defer(
                    task,
                    queue,
                    task.get("priority", 0),
                    blocked,
                    "dequeue",
                )
                continue

            self._in_flight[task["id"]] = task
            self._record_audit("dispatch", task, queue, "dispatched")
            return task
        return None

    def set_dependency_health(
        self,
        dependency: str,
        healthy: bool,
        reason: str = "",
    ) -> None:
        dependency = self._normalize_dependency(dependency)
        self._dependency_health[dependency] = {
            "healthy": bool(healthy),
            "reason": self._sanitize(reason),
            "updated_at": time.time(),
        }
        self._record_audit(
            "dependency_health",
            {"id": dependency, "type": "dependency"},
            "scheduler",
            "healthy" if healthy else "unhealthy",
            dependencies=[dependency],
            reason=reason,
        )
        if healthy:
            self._release_deferred()

    def dependency_health(self, dependency: str) -> Dict:
        dependency = self._normalize_dependency(dependency)
        return dict(self._dependency_health.get(dependency, {"healthy": True}))

    def audit_records(self) -> List[Dict]:
        return list(self._audit)

    def deferred_count(self) -> int:
        return len(self._deferred)

    def deferred_task_ids(self) -> List[str]:
        return list(self._deferred.keys())

    def in_flight_count(self) -> int:
        return len(self._in_flight)

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

    def _promote_due_scheduled(self, queue: str) -> None:
        now = time.time()
        expired = [
            task_id for task_id, record in self._scheduled.items()
            if record["ready_at"] <= now
        ]
        for task_id in expired:
            record = self._scheduled.pop(task_id)
            task = record["task"]
            target_queue = record.get("queue", queue)
            priority = record.get("priority", task.get("priority", 0))
            blocked = self._blocked_dependencies(task)
            if blocked:
                self._defer(task, target_queue, priority, blocked, "schedule")
            else:
                self._push_ready(task, target_queue, priority)

    def _push_ready(self, task: Dict, queue: str, priority: int) -> None:
        task["scheduler_state"] = "ready"
        if queue not in self._queues:
            self._queues[queue] = PriorityQueue()
        self._queues[queue].push(task, priority)

    def _defer(
        self,
        task: Dict,
        queue: str,
        priority: int,
        dependencies: List[str],
        source: str,
    ) -> None:
        task["scheduler_state"] = "deferred"
        self._deferred[task["id"]] = {
            "task": task,
            "queue": queue,
            "priority": priority,
            "dependencies": list(dependencies),
            "deferred_at": time.time(),
        }
        self._record_audit(
            "defer",
            task,
            queue,
            f"deferred during {source}",
            dependencies=dependencies,
        )

    def _release_deferred(self) -> None:
        for task_id, record in list(self._deferred.items()):
            task = record["task"]
            blocked = self._blocked_dependencies(task)
            if blocked:
                record["dependencies"] = list(blocked)
                continue

            self._deferred.pop(task_id)
            self._push_ready(
                task,
                record["queue"],
                record.get("priority", task.get("priority", 0)),
            )
            self._record_audit(
                "release_deferred",
                task,
                record["queue"],
                "dependency health restored",
                dependencies=record.get("dependencies", []),
            )

    def _blocked_dependencies(self, task: Dict) -> List[str]:
        blocked = []
        for dependency in self._task_dependencies(task):
            health = self._dependency_health.get(dependency)
            if health and not health.get("healthy", True):
                blocked.append(dependency)
        return blocked

    def _task_dependencies(self, task: Dict) -> List[str]:
        raw = (
            task.get("required_services")
            or task.get("external_services")
            or task.get("dependencies")
            or []
        )
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, dict):
            raw = raw.keys()
        return sorted({
            self._normalize_dependency(dependency)
            for dependency in raw
            if self._normalize_dependency(dependency)
        })

    def _record_audit(
        self,
        action: str,
        task: Dict,
        queue: str,
        decision: str,
        dependencies: Optional[List[str]] = None,
        reason: str = "",
    ) -> None:
        self._audit.append({
            "action": action,
            "task_id": str(task.get("id", "")),
            "task_type": self._sanitize(task.get("type", "unknown")),
            "queue": self._sanitize(queue),
            "decision": self._sanitize(decision),
            "dependencies": [
                self._sanitize(dependency)
                for dependency in (
                    dependencies or self._task_dependencies(task)
                )
            ],
            "reason": self._sanitize(reason),
            "recorded_at": time.time(),
        })

    def _normalize_dependency(self, dependency: Any) -> str:
        return str(dependency).strip().lower()

    def _sanitize(self, value: Any) -> str:
        return str(value).replace("\n", " ").replace("\r", " ")[:120]

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
