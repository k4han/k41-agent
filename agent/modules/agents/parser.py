"""Parser for agent Markdown files with YAML frontmatter."""

from __future__ import annotations

import logging
import re
import yaml
from pathlib import Path

from agent.modules.agents.models import AgentConfig
from agent.shared.infrastructure.parsing import parse_string_or_list
from agent.modules.workflows import (
    ROUTER_GRAPH_TYPE,
    REACT_AGENT_GRAPH_TYPE,
)

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(
    r"\A\s*---\s*\n(.*?)\n---\s*\n?(.*)",
    re.DOTALL,
)


class AgentMarkdownError(ValueError):
    """Raised when an agent Markdown document cannot be parsed."""


def parse_agent_markdown_content(
    content: str,
    *,
    source_label: str = "<memory>",
    strict_router_template: bool = False,
) -> AgentConfig:
    """Parse agent Markdown content and raise on invalid input."""
    match = _FRONTMATTER_RE.match(content)
    if not match:
        raise AgentMarkdownError(f"Agent file {source_label} has no valid frontmatter.")

    yaml_block = match.group(1)
    body = match.group(2).strip()

    try:
        data = yaml.safe_load(yaml_block)
    except Exception as exc:
        raise AgentMarkdownError(
            f"Agent file {source_label} has unparseable YAML: {exc}."
        ) from exc

    if not isinstance(data, dict):
        raise AgentMarkdownError(
            f"Agent file {source_label} frontmatter is not a mapping."
        )

    return _build_agent_config(
        data,
        body,
        source_label=source_label,
        strict_router_template=strict_router_template,
    )


def parse_agent_file(path: str | Path) -> AgentConfig | None:
    """Parse a Markdown agent file and return AgentConfig, or None on failure.

    Expected format:
        ---
        name: my_agent
        graph_type: react_agent  # optional, defaults to the registered react_agent workflow
        ...
        ---
        System prompt body here...
    """
    filepath = Path(path)
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to read agent file %s: %s", filepath, e)
        return None

    try:
        return parse_agent_markdown_content(content, source_label=str(filepath))
    except AgentMarkdownError as e:
        logger.warning("%s — skipping.", e)
        return None
    except Exception as e:
        logger.warning("Failed to validate AgentConfig for %s: %s — skipping.", filepath, e)
        return None


def parse_agent_file_with_error(
    path: str | Path,
    *,
    strict_router_template: bool = False,
) -> tuple[AgentConfig | None, str]:
    """Parse a file and return either an AgentConfig or a user-facing error."""
    filepath = Path(path)
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception as exc:
        return None, f"Failed to read agent file: {exc}"

    try:
        return (
            parse_agent_markdown_content(
                content,
                source_label=str(filepath),
                strict_router_template=strict_router_template,
            ),
            "",
        )
    except Exception as exc:
        return None, str(exc)


def serialize_agent_config(config: AgentConfig) -> str:
    """Serialize an AgentConfig to canonical Markdown with YAML frontmatter."""
    data: dict[str, object] = {
        "name": config.name,
        "display_name": config.display_name,
        "description": config.description,
        "graph_type": config.graph_type,
        "provider": config.provider,
        "model": config.model,
        "tools": list(config.tools),
        "context_trim_threshold": config.context_trim_threshold,
    }
    if config.mcp_servers is not None:
        data["mcp_servers"] = list(config.mcp_servers)
    if config.sub_agents is not None:
        data["sub_agents"] = list(config.sub_agents)
    if config.plan_approval_targets:
        data["plan_approval_targets"] = list(config.plan_approval_targets)
    if config.hidden:
        data["hidden"] = True

    yaml_block = yaml.safe_dump(
        data,
        allow_unicode=False,
        default_flow_style=False,
        sort_keys=False,
    )
    body = config.system_prompt.strip()
    return f"---\n{yaml_block}---\n\n{body}\n"


def _build_agent_config(
    data: dict,
    body: str,
    *,
    source_label: str,
    strict_router_template: bool,
) -> AgentConfig:
    # Required field
    name = data.get("name")
    # graph_type is optional for agent-name-first flows.
    graph_type = data.get("graph_type") or REACT_AGENT_GRAPH_TYPE

    if not name:
        raise AgentMarkdownError(f"Agent file {source_label} is missing 'name'.")

    name = str(name).strip()
    graph_type = str(graph_type).strip() or REACT_AGENT_GRAPH_TYPE

    provider = str(data.get("provider", "")).strip()
    if not provider:
        raise AgentMarkdownError(
            f"Agent file {source_label} is missing required 'provider'. "
            'Use provider: "default" to use llm.default_provider.'
        )

    # Optional fields with defaults
    display_name = str(data.get("display_name", ""))
    description = str(data.get("description", ""))
    model = str(data.get("model", "")).strip()

    raw_threshold = data.get("context_trim_threshold")
    if raw_threshold is None:
        raw_threshold = data.get("max_context_tokens")

    try:
        context_trim_threshold = int(raw_threshold if raw_threshold is not None else 50_000)
    except (TypeError, ValueError) as exc:
        raise AgentMarkdownError(
            f"Agent file {source_label} has invalid 'context_trim_threshold'."
        ) from exc
    tools = parse_string_or_list(data.get("tools", []))
    raw_mcp = data.get("mcp_servers")
    if raw_mcp is None:
        mcp_servers = None
    else:
        mcp_servers = parse_string_or_list(raw_mcp) or []

    # sub_agents: None means leaf (cannot call anyone), list means can call those
    raw_sub = data.get("sub_agents")
    if raw_sub is None:
        sub_agents = None
    else:
        sub_agents = parse_string_or_list(raw_sub) or []

    plan_approval_targets = parse_string_or_list(
        data.get("plan_approval_targets", [])
    )
    hidden = bool(data.get("hidden", False))

    # Validate router agent template at parse time
    if graph_type == ROUTER_GRAPH_TYPE and body:
        _validate_router_template(
            body,
            name,
            strict=strict_router_template,
        )

    try:
        return AgentConfig(
            name=name,
            display_name=display_name,
            description=description,
            graph_type=graph_type,
            provider=provider,
            model=model,
            tools=tools,
            mcp_servers=mcp_servers,
            sub_agents=sub_agents,
            plan_approval_targets=plan_approval_targets,
            hidden=hidden,
            context_trim_threshold=context_trim_threshold,
            system_prompt=body,
        )
    except Exception as exc:
        raise AgentMarkdownError(
            f"Failed to validate AgentConfig for {source_label}: {exc}."
        ) from exc


def _validate_router_template(
    template: str,
    agent_name: str,
    *,
    strict: bool = False,
) -> None:
    """Validate router agent template has required placeholders."""
    required_placeholders = ["{agent_options}", "{user_input}"]
    missing = [p for p in required_placeholders if p not in template]
    if missing:
        joined = ", ".join(missing)
        if strict:
            raise AgentMarkdownError(
                f"Router agent '{agent_name}' system_prompt is missing required placeholders: {joined}."
            )
        logger.warning(
            "Router agent '%s' system_prompt is missing required placeholders: %s",
            agent_name,
            joined,
        )


__all__ = [
    "AgentMarkdownError",
    "parse_agent_file",
    "parse_agent_file_with_error",
    "parse_agent_markdown_content",
    "serialize_agent_config",
]
