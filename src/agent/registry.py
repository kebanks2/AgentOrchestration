"""Agent Registry — Manages agent lifecycle and metadata."""

import logging
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


logger = logging.getLogger(__name__)


class AgentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"
    TERMINATED = "terminated"


class HandlerVersionError(ValueError):
    """Raised when a handler registration violates version compatibility."""


class AgentRegistry:
    def __init__(self, storage_backend: str = "memory"):
        self.storage_backend = storage_backend
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._index: Dict[str, List[str]] = {}
        self._handler_versions: Dict[str, str] = {}
        self._resolution_cache: Dict[Tuple[str, Optional[str]], str] = {}
        self._audit_records: List[Dict[str, Any]] = []
        self._registry_metrics: Dict[str, int] = {
            "handler_version_rejections": 0,
            "handler_cache_invalidations": 0,
        }

    def register(
        self,
        name: str,
        agent_type: str,
        config: Optional[Dict] = None,
    ) -> str:
        config = dict(config or {})
        handler_version = self._handler_version(config)
        previous_version = self._handler_versions.get(agent_type)
        self._enforce_handler_version_policy(
            agent_type,
            handler_version,
            previous_version,
        )

        agent_id = str(uuid.uuid4())
        timestamp = time.time()
        self._agents[agent_id] = {
            "id": agent_id,
            "name": name,
            "type": agent_type,
            "status": AgentStatus.PENDING.value,
            "config": config,
            "created_at": timestamp,
            "updated_at": timestamp,
            "version": handler_version,
            "metrics": {"tasks_completed": 0, "errors": 0, "uptime": 0},
        }
        group = agent_type.split(".")[0]
        if group not in self._index:
            self._index[group] = []
        self._index[group].append(agent_id)
        if previous_version != handler_version:
            self._handler_versions[agent_type] = handler_version
            self._invalidate_resolution_cache(agent_type)
        return agent_id

    def get(self, agent_id: str) -> Optional[Dict[str, Any]]:
        return self._agents.get(agent_id)

    def resolve_handler(
        self,
        agent_type: str,
        required_version: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        cache_key = (agent_type, required_version)
        cached_agent_id = self._resolution_cache.get(cache_key)
        if cached_agent_id:
            cached_agent = self._agents.get(cached_agent_id)
            if cached_agent and self._is_resolvable(
                cached_agent,
                required_version,
            ):
                return cached_agent
            self._resolution_cache.pop(cache_key, None)

        candidates = [
            agent
            for agent in self._agents.values()
            if agent["type"] == agent_type
            and self._is_resolvable(agent, required_version)
        ]
        if not candidates:
            self._record_policy_decision(
                "handler_resolution_rejected",
                agent_type,
                required_version,
                "no compatible handler version registered",
            )
            return None

        selected = max(
            candidates,
            key=lambda agent: self._parse_version(agent["version"]),
        )
        self._resolution_cache[cache_key] = selected["id"]
        return selected

    def list(
        self,
        status: Optional[AgentStatus] = None,
        group: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        agents = self._agents.values()
        if status:
            agents = [a for a in agents if a["status"] == status.value]
        if group:
            agent_ids = self._index.get(group, [])
            agents = [a for a in agents if a["id"] in agent_ids]
        return list(agents)

    def update_status(self, agent_id: str, status: AgentStatus) -> bool:
        if agent_id not in self._agents:
            return False
        self._agents[agent_id]["status"] = status.value
        self._agents[agent_id]["updated_at"] = time.time()
        return True

    def delete(self, agent_id: str) -> bool:
        if agent_id not in self._agents:
            return False
        agent = self._agents.pop(agent_id)
        group = agent["type"].split(".")[0]
        if group in self._index and agent_id in self._index[group]:
            self._index[group].remove(agent_id)
        self._invalidate_resolution_cache(agent["type"])
        return True

    def count(self) -> int:
        return len(self._agents)

    def audit_records(self) -> List[Dict[str, Any]]:
        return list(self._audit_records)

    def registry_metrics(self) -> Dict[str, int]:
        return dict(self._registry_metrics)

    def _handler_version(self, config: Dict[str, Any]) -> str:
        version = config.get(
            "handler_version",
            config.get("protocol_version", config.get("version", "1.0.0")),
        )
        version = str(version)
        return self._format_version(self._parse_version(version))

    def _enforce_handler_version_policy(
        self,
        agent_type: str,
        requested_version: str,
        current_version: Optional[str],
    ) -> None:
        if current_version is None or requested_version == current_version:
            return

        requested = self._parse_version(requested_version)
        current = self._parse_version(current_version)
        if requested < current:
            self._reject_handler_version(
                agent_type,
                requested_version,
                "stale handler version",
            )

        active_agents = self._active_agents(agent_type)
        if requested[0] != current[0] and active_agents:
            self._reject_handler_version(
                agent_type,
                requested_version,
                "incompatible major version while handlers are active",
            )

    def _reject_handler_version(
        self,
        agent_type: str,
        version: str,
        reason: str,
    ) -> None:
        self._registry_metrics["handler_version_rejections"] += 1
        self._record_policy_decision(
            "handler_registration_rejected",
            agent_type,
            version,
            reason,
        )
        raise HandlerVersionError(reason)

    def _is_resolvable(
        self,
        agent: Dict[str, Any],
        required_version: Optional[str],
    ) -> bool:
        if agent["status"] in {
            AgentStatus.FAILED.value,
            AgentStatus.TERMINATED.value,
        }:
            return False
        if required_version is None:
            return True

        requested = self._parse_version(required_version)
        available = self._parse_version(agent["version"])
        return available[0] == requested[0] and available >= requested

    def _active_agents(self, agent_type: str) -> List[Dict[str, Any]]:
        return [
            agent
            for agent in self._agents.values()
            if agent["type"] == agent_type
            and agent["status"] in {
                AgentStatus.PENDING.value,
                AgentStatus.RUNNING.value,
                AgentStatus.PAUSED.value,
            }
        ]

    def _invalidate_resolution_cache(self, agent_type: str) -> None:
        keys = [key for key in self._resolution_cache if key[0] == agent_type]
        for key in keys:
            self._resolution_cache.pop(key, None)
        if keys:
            self._registry_metrics["handler_cache_invalidations"] += len(keys)
            self._record_policy_decision(
                "handler_resolution_cache_invalidated",
                agent_type,
                self._handler_versions.get(agent_type),
                "handler version changed",
            )

    def _record_policy_decision(
        self,
        event: str,
        agent_type: str,
        requested_version: Optional[str],
        reason: str,
    ) -> None:
        record = {
            "event": event,
            "agent_type": agent_type,
            "requested_version": requested_version,
            "known_version": self._handler_versions.get(agent_type),
            "reason": reason,
            "timestamp": time.time(),
        }
        self._audit_records.append(record)
        logger.warning(
            (
                "agent handler registry policy decision: "
                "%s type=%s version=%s reason=%s"
            ),
            event,
            agent_type,
            requested_version,
            reason,
        )

    def _parse_version(self, version: str) -> Tuple[int, int, int]:
        parts = version.split(".")
        if len(parts) == 2:
            parts.append("0")
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            raise HandlerVersionError(
                "handler version must use MAJOR.MINOR or MAJOR.MINOR.PATCH"
            )
        return int(parts[0]), int(parts[1]), int(parts[2])

    def _format_version(self, version: Tuple[int, int, int]) -> str:
        return ".".join(str(part) for part in version)

# 2019-01-29T11:24:49 update

# 2019-04-09T13:38:38 update

# 2019-04-11T11:24:12 update

# 2019-06-26T17:03:48 update

# 2019-07-03T14:55:48 update

# 2019-07-18T18:18:47 update

# 2019-11-05T11:27:19 update

# 2019-11-20T11:35:05 update

# 2019-11-23T15:28:54 update

# 2020-03-13T09:23:07 update

# 2020-03-30T19:31:18 update

# 2020-04-22T15:03:30 update

# 2020-07-21T10:00:48 update

# 2020-09-10T09:02:08 update

# 2020-09-10T13:39:12 update

# 2020-09-22T16:27:52 update

# 2020-10-15T10:33:14 update

# 2021-05-13T11:15:56 update

# 2021-07-07T14:57:13 update

# 2021-07-13T15:15:19 update

# 2021-07-27T10:18:16 update

# 2022-03-11T15:24:11 update

# 2022-09-22T13:24:20 update

# 2022-11-01T12:20:40 update

# 2023-01-30T12:32:27 update

# 2023-03-10T09:43:50 update

# 2023-05-10T14:28:01 update

# 2023-05-11T20:04:46 update

# 2023-05-30T17:00:59 update

# 2023-07-13T17:54:32 update

# 2023-07-20T19:04:20 update

# 2023-07-31T17:00:02 update

# 2023-09-05T19:42:07 update

# 2024-01-02T10:29:47 update

# 2024-09-17T12:45:29 update

# 2024-09-17T11:51:01 update

# 2024-11-06T18:20:15 update

# 2025-01-12T15:13:14 update

# 2025-01-14T20:24:39 update

# 2025-03-26T20:21:27 update

# 2025-04-10T18:27:06 update

# 2025-06-19T20:34:58 update

# 2025-06-21T20:23:53 update

# 2025-06-24T20:30:30 update

# 2025-07-03T13:28:03 update

# 2025-07-24T17:42:21 update

# 2025-08-19T17:42:23 update

# 2025-08-21T11:06:52 update

# 2025-10-24T09:10:08 update

# 2025-12-18T19:34:38 update

# 2026-02-06T11:22:22 update

# 2026-02-13T15:42:04 update

# 2026-04-10T08:16:30 update

# 2026-04-29T18:16:11 update
