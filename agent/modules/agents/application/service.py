"""Agent catalog service — business logic for sub-agent management."""

from __future__ import annotations

from agent.modules.agents.domain.subagent import AgentConfig
from agent.modules.agents.infrastructure.repository import get_repository


class AgentCatalogService:
    """Manages agent configs loaded from MD files and enforces call_agent rules."""

    def __init__(self):
        self._repository = get_repository()

    def get_agent(self, name: str) -> AgentConfig | None:
        """Get agent config by name. Returns None if not found."""
        return self._repository.get_cached().get(name)

    def list_agents(self) -> list[AgentConfig]:
        """Return all loaded agent configs."""
        return list(self._repository.get_cached().values())

    def get_callable_agents(self, for_agent_name: str) -> list[str]:
        """Return list of agent names that `for_agent_name` is allowed to call.

        Rules:
        - sub_agents is None → leaf node → cannot call anyone → returns []
        - sub_agents is a list (even empty) → can only call agents in that list
        """
        config = self.get_agent(for_agent_name)
        if config is None:
            return []
        if config.sub_agents is None:
            return []
        # Validate that listed sub_agents actually exist
        loaded = set(self._repository.get_cached().keys())
        return [s for s in config.sub_agents if s in loaded]

    def validate_call(self, caller_name: str, target_name: str) -> bool:
        """Check whether `caller_name` is allowed to call `target_name`."""
        if caller_name == target_name:
            return False  # prevent self-calls
        config = self.get_agent(caller_name)
        if config is None:
            return False
        if config.sub_agents is None:
            return False  # leaf → cannot call anyone
        # If sub_agents is a list (even empty), target must be in it
        if target_name not in config.sub_agents:
            return False
        # Target must also exist
        return self.get_agent(target_name) is not None

    def reload_agents(self) -> list[AgentConfig]:
        """Re-scan directory and update cache."""
        self._repository.reload()
        return self.list_agents()


# --- Module-level singleton ---

_service: AgentCatalogService | None = None


def get_catalog_service() -> AgentCatalogService:
    global _service
    if _service is None:
        _service = AgentCatalogService()
    return _service
