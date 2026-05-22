"""Shared validation and service helpers for agent API routes."""

import uuid
from typing import Dict, Optional

from src.agent import AgentRegistry, AgentStatus


class ApiValidationError(ValueError):
    def __init__(self, message: str, code: str = "validation_error"):
        super().__init__(message)
        self.code = code
        self.message = message


class AgentApiService:
    def __init__(self, registry: AgentRegistry):
        self.registry = registry

    def list_agents(
        self,
        status: Optional[str] = None,
        group: Optional[str] = None,
    ) -> Dict:
        status_filter = self._validate_status(status)
        agents = self.registry.list(status=status_filter, group=group)
        return {"agents": agents}

    def register_agent(
        self,
        name: str,
        agent_type: str,
        config: Optional[Dict] = None,
    ) -> Dict:
        self._validate_required_text("name", name)
        self._validate_required_text("agent_type", agent_type)
        agent_id = self.registry.register(name, agent_type, config)
        return {"agent_id": agent_id, "status": "registered"}

    def get_agent(self, agent_id: str) -> Dict:
        validated_agent_id = self._validate_agent_id(agent_id)
        agent = self.registry.get(validated_agent_id)
        if not agent:
            raise LookupError("Agent not found")
        return agent

    def delete_agent(self, agent_id: str) -> Dict:
        validated_agent_id = self._validate_agent_id(agent_id)
        if not self.registry.delete(validated_agent_id):
            raise LookupError("Agent not found")
        return {"status": "deleted"}

    def start_agent(self, agent_id: str) -> Dict:
        validated_agent_id = self._validate_agent_id(agent_id)
        if not self.registry.update_status(
            validated_agent_id,
            AgentStatus.RUNNING,
        ):
            raise LookupError("Agent not found")
        return {"status": "started"}

    def stop_agent(self, agent_id: str) -> Dict:
        validated_agent_id = self._validate_agent_id(agent_id)
        if not self.registry.update_status(
            validated_agent_id,
            AgentStatus.PAUSED,
        ):
            raise LookupError("Agent not found")
        return {"status": "stopped"}

    def count_agents(self) -> Dict:
        return {"count": self.registry.count()}

    def _validate_status(self, status: Optional[str]) -> Optional[AgentStatus]:
        if status is None:
            return None
        try:
            return AgentStatus(status)
        except ValueError:
            allowed = ", ".join(sorted(member.value for member in AgentStatus))
            raise ApiValidationError(
                f"status must be one of: {allowed}",
            )

    def _validate_agent_id(self, agent_id: str) -> str:
        try:
            uuid.UUID(agent_id)
        except (TypeError, ValueError):
            raise ApiValidationError("agent_id must be a valid UUID")
        return agent_id

    def _validate_required_text(self, field: str, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ApiValidationError(f"{field} must be a non-empty string")
