"""Agent configuration models loaded from Markdown files."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from agent.modules.providers import EFFORT_PATTERN


def normalize_agent_model(data: Any) -> Any:
    """Accept structured model settings while retaining the flat runtime fields."""
    if isinstance(data, dict) and isinstance(data.get("model"), dict):
        model = data["model"]
        if "id" not in model or not isinstance(model["id"], str):
            raise ValueError("Structured model settings require a string 'id'.")
        if set(model) - {"id", "effort"}:
            raise ValueError("Structured model settings only support 'id' and 'effort'.")
        if "effort" in model and "reasoning_effort" in data and model["effort"] != data["reasoning_effort"]:
            raise ValueError("Conflicting 'model.effort' and 'reasoning_effort' values.")
        data = {**data, "model": model["id"].strip()}
        if "effort" in model:
            data["reasoning_effort"] = model["effort"]
    return data


def reject_legacy_context_settings(data: Any) -> Any:
    """Reject token budgets on agent cards instead of interpreting them as percentages."""
    if isinstance(data, dict):
        legacy = sorted(set(data) & {"context_trim_threshold", "max_context_tokens"})
        if legacy:
            raise ValueError(
                f"Unsupported agent card settings: {', '.join(legacy)}. "
                "Replace them with context_compact_threshold (1-100 percent, default 75). "
                "Configure channel trimming in dashboard channel settings."
            )
    return data


class AgentConfig(BaseModel):
    """Configuration for a single agent defined via a Markdown file."""

    name: str
    display_name: str = ""
    description: str = ""
    graph_type: str  # Registered workflow name
    provider: str
    model: str = ""
    reasoning_effort: str | None = Field(default=None, pattern=EFFORT_PATTERN)
    tools: list[str] = Field(default_factory=list)
    tool_permissions: list[dict[str, Any]] | None = None
    tool_configs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    mcp_servers: Optional[list[str]] = None
    sub_agents: Optional[list[str]] = None  # None = leaf (no call_agent), list = allowed targets
    plan_approval_targets: list[str] = Field(default_factory=list)
    hidden: bool = False
    context_compact_threshold: int = Field(default=75, ge=1, le=100, strict=True)
    system_prompt: str = ""  # Markdown body content (after frontmatter)

    @field_validator("tool_permissions")
    @classmethod
    def _validate_tool_permissions(cls, rules):
        if rules is not None:
            for rule in rules:
                if rule.get("effect") not in {"allow", "ask", "deny"}:
                    raise ValueError("Permission effect must be allow, ask or deny.")
                if not isinstance(rule.get("action", "*"), str) or not isinstance(rule.get("resource", "*"), str):
                    raise ValueError("Permission action and resource must be strings.")
        return rules

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        data = normalize_agent_model(reject_legacy_context_settings(data))
        if isinstance(data, dict) and data.get("tools"):
            from agent.modules.tools import canonical_tool_names
            data = {**data, "tools": canonical_tool_names(data["tools"])}
        return data

class AgentCard(BaseModel):
    """Dashboard-facing view of an agent card file."""

    name: str
    display_name: str = ""
    description: str = ""
    graph_type: str = ""
    provider: str = ""
    model: str = ""
    reasoning_effort: str | None = Field(default=None, pattern=EFFORT_PATTERN)
    tools: list[str] = Field(default_factory=list)
    tool_permissions: list[dict[str, Any]] | None = None
    tool_configs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    mcp_servers: Optional[list[str]] = None
    sub_agents: Optional[list[str]] = None
    plan_approval_targets: list[str] = Field(default_factory=list)
    hidden: bool = False
    context_compact_threshold: int = Field(default=75, ge=1, le=100, strict=True)
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
        data = normalize_agent_model(reject_legacy_context_settings(data))
        if isinstance(data, dict) and data.get("tools"):
            from agent.modules.tools import canonical_tool_names
            data = {**data, "tools": canonical_tool_names(data["tools"])}
        return data

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
            reasoning_effort=config.reasoning_effort,
            tools=list(config.tools),
            tool_permissions=config.tool_permissions,
            tool_configs={
                name: dict(values)
                for name, values in config.tool_configs.items()
            },
            mcp_servers=list(config.mcp_servers) if config.mcp_servers is not None else None,
            sub_agents=list(config.sub_agents) if config.sub_agents is not None else None,
            plan_approval_targets=list(config.plan_approval_targets),
            hidden=config.hidden,
            context_compact_threshold=config.context_compact_threshold,
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
            reasoning_effort=self.reasoning_effort,
            tools=list(self.tools),
            tool_permissions=self.tool_permissions,
            tool_configs={
                name: dict(values)
                for name, values in self.tool_configs.items()
            },
            mcp_servers=list(self.mcp_servers) if self.mcp_servers is not None else None,
            sub_agents=list(self.sub_agents) if self.sub_agents is not None else None,
            plan_approval_targets=list(self.plan_approval_targets),
            hidden=self.hidden,
            context_compact_threshold=self.context_compact_threshold,
            system_prompt=self.system_prompt,
        )
