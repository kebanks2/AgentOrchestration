"""Task Scheduler — Priority-based task queuing and dispatch."""

import heapq
import time
from typing import Any, Dict, Optional
from uuid import uuid4

from src.common.task_state import (
    TaskState,
    TaskStateRepository,
    WorkspaceScopeRequired,
)


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
        *,
        task_state_repository: Optional[TaskStateRepository] = None,
    ):
        self._queues: Dict[tuple[str, str], PriorityQueue] = {}
        self._scheduled: Dict[
            tuple[str, str],
            tuple[float, Dict, int, str],
        ] = {}
        self._in_flight: Dict[tuple[str, str], Dict] = {}
        self._task_state = task_state_repository or TaskStateRepository()
        self._max_retries = 3

    def enqueue(
        self,
        task: Dict,
        queue: str = "default",
        priority: int = 0,
        *,
        workspace_id: str,
    ) -> str:
        workspace_id = self._require_workspace(workspace_id)
        task_id = str(uuid4())
        task["id"] = task_id
        task["workspace_id"] = workspace_id
        task["enqueued_at"] = time.time()
        task["retries"] = 0
        task["priority"] = priority

        queue_key = (workspace_id, queue)
        if queue_key not in self._queues:
            self._queues[queue_key] = PriorityQueue()
        self._queues[queue_key].push(task, priority)
        self._task_state.put(workspace_id, task_id, "queued", payload=task)
        return task_id

    def schedule(
        self,
        task: Dict,
        delay: float,
        queue: str = "default",
        priority: int = 0,
        *,
        workspace_id: str,
    ) -> str:
        workspace_id = self._require_workspace(workspace_id)
        task_id = str(uuid4())
        task["id"] = task_id
        task["workspace_id"] = workspace_id
        task["retries"] = 0
        task["priority"] = priority
        self._scheduled[(workspace_id, task_id)] = (
            time.time() + delay,
            task,
            priority,
            queue,
        )
        self._task_state.put(workspace_id, task_id, "scheduled", payload=task)
        return task_id

    async def dequeue(
        self,
        queue: str = "default",
        timeout: float = 1.0,
        *,
        workspace_id: str,
    ) -> Optional[Dict]:
        workspace_id = self._require_workspace(workspace_id)
        now = time.time()
        expired = [
            key
            for key, (
                scheduled_at,
                _task,
                _priority,
                _queue,
            ) in self._scheduled.items()
            if key[0] == workspace_id and scheduled_at <= now
        ]
        for key in expired:
            (
                _scheduled_at,
                task,
                task_priority,
                task_queue,
            ) = self._scheduled.pop(key)
            queue_key = (workspace_id, task_queue)
            if queue_key not in self._queues:
                self._queues[queue_key] = PriorityQueue()
            self._queues[queue_key].push(task, task_priority)
            self._task_state.update(
                workspace_id,
                task["id"],
                status="queued",
                payload=task,
            )

        queue_key = (workspace_id, queue)
        if queue_key in self._queues and len(self._queues[queue_key]) > 0:
            task = self._queues[queue_key].pop()
            if task:
                self._in_flight[(workspace_id, task["id"])] = task
                self._task_state.update(
                    workspace_id,
                    task["id"],
                    status="running",
                    payload=task,
                )
                return task
        return None

    def complete(self, task_id: str, *, workspace_id: str) -> bool:
        workspace_id = self._require_workspace(workspace_id)
        task = self._in_flight.pop((workspace_id, task_id), None)
        if task is None:
            return False
        self._task_state.update(
            workspace_id,
            task_id,
            status="completed",
            payload=task,
        )
        return True

    def fail(
        self,
        task_id: str,
        queue: str = "default",
        *,
        workspace_id: str,
    ) -> bool:
        workspace_id = self._require_workspace(workspace_id)
        task = self._in_flight.pop((workspace_id, task_id), None)
        if task:
            task["retries"] += 1
            if task["retries"] < self._max_retries:
                queue_key = (workspace_id, queue)
                if queue_key not in self._queues:
                    self._queues[queue_key] = PriorityQueue()
                self._queues[queue_key].push(task, task.get("priority", 0))
                self._task_state.update(
                    workspace_id,
                    task_id,
                    status="queued",
                    payload=task,
                )
                return True
            self._task_state.update(
                workspace_id,
                task_id,
                status="failed",
                payload=task,
            )
        return False

    def get_state(
        self,
        task_id: str,
        *,
        workspace_id: str,
    ) -> Optional[TaskState]:
        workspace_id = self._require_workspace(workspace_id)
        return self._task_state.get(workspace_id, task_id)

    def get_state_by_task_id(self, task_id: str) -> TaskState:
        raise WorkspaceScopeRequired(
            "Task state queries must include workspace_id"
        )

    def _require_workspace(self, workspace_id: str) -> str:
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise WorkspaceScopeRequired("workspace_id is required")
        return workspace_id.strip()

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
