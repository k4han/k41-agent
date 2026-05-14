"""Agent configuration models loaded from Markdown files."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    """Configuration for a single agent defined via a Markdown file."""

    name: str
    display_name: str = ""
    description: str = ""
    graph_type: str  # Registered workflow name
    model: str = ""
    tools: list[str] = Field(default_factory=list)
    sub_agents: Optional[list[str]] = None  # None = leaf (no call_agent), list = allowed targets
    max_context_tokens: int = 50_000
    routing_hints: str = ""
    capabilities: list[str] = Field(default_factory=list)
    system_prompt: str = ""  # Markdown body content (after frontmatter)


class AgentCard(BaseModel):
    """Dashboard-facing view of an agent card file."""

    name: str
    display_name: str = ""
    description: str = ""
    graph_type: str = ""
    model: str = ""
    tools: list[str] = Field(default_factory=list)
    sub_agents: Optional[list[str]] = None
    max_context_tokens: int = 50_000
    routing_hints: str = ""
    capabilities: list[str] = Field(default_factory=list)
    system_prompt: str = ""
    source: Literal["builtin", "user"]
    path: str
    editable: bool = False
    overrides_builtin: bool = False
    valid: bool = True
    error: str = ""

    @classmethod
    def from_config(
        cls,
        config: AgentConfig,
        *,
        source: Literal["builtin", "user"],
        path: str,
        editable: bool,
        overrides_builtin: bool = False,
    ) -> "AgentCard":
        """Build a dashboard DTO from a parsed agent config."""
        return cls(
            name=config.name,
            display_name=config.display_name,
            description=config.description,
            graph_type=config.graph_type,
            model=config.model,
            tools=list(config.tools),
            sub_agents=list(config.sub_agents) if config.sub_agents is not None else None,
            max_context_tokens=config.max_context_tokens,
            routing_hints=config.routing_hints,
            capabilities=list(config.capabilities),
            system_prompt=config.system_prompt,
            source=source,
            path=path,
            editable=editable,
            overrides_builtin=overrides_builtin,
            valid=True,
            error="",
        )

    @classmethod
    def invalid(
        cls,
        *,
        name: str,
        source: Literal["builtin", "user"],
        path: str,
        editable: bool,
        error: str,
    ) -> "AgentCard":
        """Build a dashboard DTO for a file that could not be parsed."""
        return cls(
            name=name,
            source=source,
            path=path,
            editable=editable,
            valid=False,
            error=error,
        )

    def to_agent_config(self) -> AgentConfig:
        """Return this valid card as an AgentConfig."""
        if not self.valid:
            raise ValueError(f"Cannot convert invalid agent card '{self.name}'.")
        return AgentConfig(
            name=self.name,
            display_name=self.display_name,
            description=self.description,
            graph_type=self.graph_type,
            model=self.model,
            tools=list(self.tools),
            sub_agents=list(self.sub_agents) if self.sub_agents is not None else None,
            max_context_tokens=self.max_context_tokens,
            routing_hints=self.routing_hints,
            capabilities=list(self.capabilities),
            system_prompt=self.system_prompt,
        )
