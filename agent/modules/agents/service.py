"""Agent catalog service — business logic for sub-agent management."""

from __future__ import annotations

import re

from agent.modules.agents.models import AgentCard, AgentConfig
from agent.modules.agents.parser import serialize_agent_config, parse_agent_markdown_content
from agent.modules.agents.repository import get_repository
from agent.modules.workflows import ROUTER_GRAPH_TYPE


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

    def list_agent_cards(self) -> list[AgentCard]:
        """Return effective agent cards with source and file metadata."""
        return self._repository.list_cards()

    def get_agent_card(self, name: str) -> AgentCard | None:
        """Return one effective dashboard card by agent name."""
        return self._repository.get_card(name)

    def create_agent_card(self, config: AgentConfig) -> AgentCard:
        """Create a new user-editable agent card."""
        self._validate_for_save(config)
        return self._repository.create_user_agent(config)

    def update_agent_card(self, name: str, config: AgentConfig) -> AgentCard:
        """Update an existing user-editable agent card."""
        normalized_name = self._validate_agent_name(name)
        if config.name != normalized_name:
            raise ValueError("Agent name cannot be changed.")
        self._validate_for_save(config)
        return self._repository.update_user_agent(normalized_name, config)

    def delete_agent_card(self, name: str) -> None:
        """Delete a user-editable agent card."""
        normalized_name = self._validate_agent_name(name)
        self._repository.delete_user_agent(normalized_name)

    def clone_builtin_agent(self, name: str) -> AgentCard:
        """Clone a readonly builtin card into the user agents directory."""
        normalized_name = self._validate_agent_name(name)
        return self._repository.clone_builtin_agent(normalized_name)

    @staticmethod
    def _validate_agent_name(name: str) -> str:
        normalized = str(name or "").strip()
        if not normalized:
            raise ValueError("Agent name is required.")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", normalized):
            raise ValueError(
                "Agent name can only contain letters, numbers, underscores, and hyphens."
            )
        return normalized

    def _validate_for_save(self, config: AgentConfig) -> None:
        normalized_name = self._validate_agent_name(config.name)
        if config.name != normalized_name:
            raise ValueError("Agent name cannot include leading or trailing whitespace.")
        if not config.graph_type.strip():
            raise ValueError("Graph type is required.")
        if not config.provider.strip():
            raise ValueError('Provider is required. Use "default" to use llm.default_provider.')
        if config.max_context_tokens <= 0:
            raise ValueError("max_context_tokens must be greater than 0.")
        if config.graph_type == ROUTER_GRAPH_TYPE:
            for placeholder in ("{agent_options}", "{user_input}"):
                if placeholder not in config.system_prompt:
                    raise ValueError(
                        "Router agent system_prompt must include {agent_options} and {user_input}."
                    )

        # Round-trip through the Markdown parser so dashboard saves follow the
        # same contract used by runtime file discovery.
        parse_agent_markdown_content(
            serialize_agent_config(config),
            source_label=f"agent:{config.name}",
            strict_router_template=True,
        )


# --- Module-level singleton ---

_service: AgentCatalogService | None = None


def get_catalog_service() -> AgentCatalogService:
    global _service
    if _service is None:
        _service = AgentCatalogService()
    return _service
