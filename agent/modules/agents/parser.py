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

    match = _FRONTMATTER_RE.match(content)
    if not match:
        logger.warning("Agent file %s has no valid frontmatter — skipping.", filepath)
        return None

    yaml_block = match.group(1)
    body = match.group(2).strip()

    try:
        data = yaml.safe_load(yaml_block)
    except Exception as e:
        logger.warning("Agent file %s has unparseable YAML: %s — skipping.", filepath, e)
        return None

    if not isinstance(data, dict):
        logger.warning("Agent file %s frontmatter is not a mapping — skipping.", filepath)
        return None

    # Required field
    name = data.get("name")
    # graph_type is optional for agent-name-first flows.
    graph_type = data.get("graph_type") or REACT_AGENT_GRAPH_TYPE

    if not name:
        logger.warning("Agent file %s missing 'name' — skipping.", filepath)
        return None

    name = str(name).strip()
    graph_type = str(graph_type).strip() or REACT_AGENT_GRAPH_TYPE

    # Optional fields with defaults
    display_name = str(data.get("display_name", ""))
    description = str(data.get("description", ""))
    model = str(data.get("model", "")).strip()
    max_context_tokens = int(data.get("max_context_tokens", 50_000))
    routing_hints = str(data.get("routing_hints", ""))

    capabilities = parse_string_or_list(data.get("capabilities", []))
    tools = parse_string_or_list(data.get("tools", []))

    # sub_agents: None means leaf (cannot call anyone), list means can call those
    raw_sub = data.get("sub_agents")
    if raw_sub is None:
        sub_agents = None
    else:
        sub_agents = parse_string_or_list(raw_sub) or []

    # Validate router agent template at parse time
    if graph_type == ROUTER_GRAPH_TYPE and body:
        _validate_router_template(body, name)

    try:
        return AgentConfig(
            name=name,
            display_name=display_name,
            description=description,
            graph_type=graph_type,
            model=model,
            tools=tools,
            sub_agents=sub_agents,
            max_context_tokens=max_context_tokens,
            routing_hints=routing_hints,
            capabilities=capabilities,
            system_prompt=body,
        )
    except Exception as e:
        logger.warning("Failed to validate AgentConfig for %s: %s — skipping.", filepath, e)
        return None


def _validate_router_template(template: str, agent_name: str) -> None:
    """Validate router agent template has required placeholders."""
    required_placeholders = ["{agent_options}", "{user_input}"]
    missing = [p for p in required_placeholders if p not in template]
    if missing:
        joined = ", ".join(missing)
        logger.warning(
            "Router agent '%s' system_prompt is missing required placeholders: %s",
            agent_name,
            joined,
        )


__all__ = ["parse_agent_file"]
