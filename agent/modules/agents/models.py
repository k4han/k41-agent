"""Agent configuration models loaded from Markdown files."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


def _normalize_max_context_tokens(data: Any) -> Any:
    """Mirror ``max_context_tokens`` into ``context_trim_threshold`` for legacy inputs."""
    if not isinstance(data, dict):
        return data
    if "max_context_tokens" in data and "context_trim_threshold" not in data:
        return {**data, "context_trim_threshold": data["max_context_tokens"]}
    return data


class AgentConfig(BaseModel):
    """Configuration for a single agent defined via a Markdown file."""

    name: str
    display_name: str = ""
    description: str = ""
    graph_type: str  # Registered workflow name
    provider: str
    model: str = ""
    tools: list[str] = Field(default_factory=list)
    tool_configs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    mcp_servers: Optional[list[str]] = None
    sub_agents: Optional[list[str]] = None  # None = leaf (no call_agent), list = allowed targets
    plan_approval_targets: list[str] = Field(default_factory=list)
    hidden: bool = False
    context_trim_threshold: int = 50_000
    # Backward-compat alias for ``context_trim_threshold``. Always mirrors the trim
    # threshold after normalization; kept as a field so it round-trips through JSON
    # for older dashboards and parsers that still read/write the legacy key.
    max_context_tokens: Optional[int] = None
    system_prompt: str = ""  # Markdown body content (after frontmatter)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        return _normalize_max_context_tokens(data)

    @model_validator(mode="after")
    def _sync_max_context_tokens(self) -> "AgentConfig":
        # Keep the legacy field consistent with the canonical threshold.
        object.__setattr__(self, "max_context_tokens", self.context_trim_threshold)
        return self


class AgentCard(BaseModel):
    """Dashboard-facing view of an agent card file."""

    name: str
    display_name: str = ""
    description: str = ""
    graph_type: str = ""
    provider: str = ""
    model: str = ""
    tools: list[str] = Field(default_factory=list)
    tool_configs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    mcp_servers: Optional[list[str]] = None
    sub_agents: Optional[list[str]] = None
    plan_approval_targets: list[str] = Field(default_factory=list)
    hidden: bool = False
    context_trim_threshold: int = 50_000
    # See ``AgentConfig.max_context_tokens``: legacy alias mirrored at validation.
    max_context_tokens: Optional[int] = None
    system_prompt: str = ""
    source: Literal["builtin", "user"]
    path: str
    editable: bool = False
    overrides_builtin: bool = False
    valid: bool = True
    error: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        return _normalize_max_context_tokens(data)

    @model_validator(mode="after")
    def _sync_max_context_tokens(self) -> "AgentCard":
        object.__setattr__(self, "max_context_tokens", self.context_trim_threshold)
        return self

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
            provider=config.provider,
            model=config.model,
            tools=list(config.tools),
            tool_configs={
                name: dict(values)
                for name, values in config.tool_configs.items()
            },
            mcp_servers=list(config.mcp_servers) if config.mcp_servers is not None else None,
            sub_agents=list(config.sub_agents) if config.sub_agents is not None else None,
            plan_approval_targets=list(config.plan_approval_targets),
            hidden=config.hidden,
            context_trim_threshold=config.context_trim_threshold,
            max_context_tokens=config.max_context_tokens,
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
            provider=self.provider,
            model=self.model,
            tools=list(self.tools),
            tool_configs={
                name: dict(values)
                for name, values in self.tool_configs.items()
            },
            mcp_servers=list(self.mcp_servers) if self.mcp_servers is not None else None,
            sub_agents=list(self.sub_agents) if self.sub_agents is not None else None,
            plan_approval_targets=list(self.plan_approval_targets),
            hidden=self.hidden,
            context_trim_threshold=self.context_trim_threshold,
            max_context_tokens=self.max_context_tokens,
            system_prompt=self.system_prompt,
        )
