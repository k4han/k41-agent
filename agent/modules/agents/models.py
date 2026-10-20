"""Agent configuration models loaded from Markdown files."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from agent.modules.providers import EFFORT_PATTERN


# Text values that mean "not configured" when they appear in agent cards.
_NULL_LIKE_TEXT = {"", "none", "null", "~"}


def coerce_agent_text(value: Any, default: str = "") -> str:
    """Coerce a nullable scalar to a trimmed string, falling back to ``default``.

    Empty strings, Python ``None``, and the literal texts ``None``, ``null`` and
    ``~`` are treated as missing so agent cards can omit optional settings
    instead of failing validation.
    """
    if value is None:
        return default
    text = str(value).strip()
    if text.lower() in _NULL_LIKE_TEXT:
        return default
    return text


def normalize_agent_defaults(data: Any) -> Any:
    """Fill nullable provider/model settings with their runtime defaults."""
    if isinstance(data, dict):
        model = data.get("model")
        if not isinstance(model, dict):
            data = {**data, "model": coerce_agent_text(model)}
        data = {**data, "provider": coerce_agent_text(data.get("provider"), "default")}
    return data


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


def reject_legacy_agent_settings(data: Any) -> Any:
    """Reject removed agent fields instead of silently ignoring them."""
    if isinstance(data, dict):
        legacy = sorted(set(data) & {"hidden", "context_trim_threshold", "max_context_tokens"})
        if legacy:
            instructions = []
            if "hidden" in legacy:
                instructions.append("Replace hidden with internal (true for system-only agents, false for chat agents).")
            if set(legacy) & {"context_trim_threshold", "max_context_tokens"}:
                instructions.append(
                    "Replace token budgets with context_compact_threshold (1-100 percent, default 75). "
                    "Configure channel trimming in dashboard channel settings."
                )
            raise ValueError(
                f"Unsupported agent card settings: {', '.join(legacy)}. "
                + " ".join(instructions)
            )
    return data


class AgentConfig(BaseModel):
    """Configuration for a single agent defined via a Markdown file."""

    name: str
    display_name: str = ""
    description: str = ""
    graph_type: str  # Registered workflow name
    provider: str = "default"
    model: str = ""
    reasoning_effort: str | None = Field(default=None, pattern=EFFORT_PATTERN)
    tools: list[str] = Field(default_factory=list)
    tool_permissions: list[dict[str, Any]] | None = None
    tool_configs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    sub_agents: Optional[list[str]] = None  # Callable targets automatically enable delegate_task.
    plan_approval_targets: list[str] = Field(default_factory=list)
    internal: bool = Field(default=False, description="Whether this agent is reserved for system use.")
    context_compact_threshold: int = Field(default=75, ge=1, le=100, strict=True)
    system_prompt: str = ""  # Markdown body content (after frontmatter)

    @model_validator(mode="after")
    def _validate_pool_effort(self):
        from agent.modules.providers.model_pools import validate_pool_effort

        validate_pool_effort(self.provider, self.reasoning_effort)
        return self

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
        data = normalize_agent_defaults(
            normalize_agent_model(reject_legacy_agent_settings(data))
        )
        if isinstance(data, dict):
            from agent.modules.tools import TOOL_ALIASES, canonical_tool_names
            if "tools" in data and data["tools"] is not None:
                tools = data["tools"]
                if isinstance(tools, (list, tuple)):
                    data = {**data, "tools": canonical_tool_names(list(tools))}
            raw_configs = data.get("tool_configs")
            if isinstance(raw_configs, dict):
                data = {
                    **data,
                    "tool_configs": {
                        TOOL_ALIASES.get(str(k), str(k)): v for k, v in raw_configs.items()
                    },
                }
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
    sub_agents: Optional[list[str]] = None
    plan_approval_targets: list[str] = Field(default_factory=list)
    internal: bool = Field(default=False, description="Whether this agent is reserved for system use.")
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
        data = normalize_agent_defaults(
            normalize_agent_model(reject_legacy_agent_settings(data))
        )
        if isinstance(data, dict):
            from agent.modules.tools import TOOL_ALIASES, canonical_tool_names
            if "tools" in data and data["tools"] is not None:
                tools = data["tools"]
                if isinstance(tools, (list, tuple)):
                    data = {**data, "tools": canonical_tool_names(list(tools))}
            raw_configs = data.get("tool_configs")
            if isinstance(raw_configs, dict):
                data = {
                    **data,
                    "tool_configs": {
                        TOOL_ALIASES.get(str(k), str(k)): v for k, v in raw_configs.items()
                    },
                }
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
            sub_agents=list(config.sub_agents) if config.sub_agents is not None else None,
            plan_approval_targets=list(config.plan_approval_targets),
            internal=config.internal,
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
            sub_agents=list(self.sub_agents) if self.sub_agents is not None else None,
            plan_approval_targets=list(self.plan_approval_targets),
            internal=self.internal,
            context_compact_threshold=self.context_compact_threshold,
            system_prompt=self.system_prompt,
        )
