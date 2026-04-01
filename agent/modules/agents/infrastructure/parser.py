"""Parser for agent Markdown files with YAML frontmatter."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from agent.modules.agents.domain.subagent import AgentConfig

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
        graph_type: react_agent  # optional, defaults to react_agent
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
        import yaml

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
    # Accept legacy alias `workflow` and default to react_agent.
    graph_type = data.get("graph_type") or data.get("workflow") or "react_agent"

    if not name:
        logger.warning("Agent file %s missing 'name' — skipping.", filepath)
        return None

    name = str(name).strip()
    graph_type = str(graph_type).strip() or "react_agent"

    # Optional fields with defaults
    display_name = str(data.get("display_name", ""))
    description = str(data.get("description", ""))
    service_type = str(data.get("service_type", "default"))
    model = str(data.get("model", "")).strip()
    max_context_tokens = int(data.get("max_context_tokens", 50_000))

    # tools: can be list of strings
    raw_tools = data.get("tools", [])
    if isinstance(raw_tools, str):
        tools = [t.strip() for t in raw_tools.split(",") if t.strip()]
    elif isinstance(raw_tools, list):
        tools = [str(t) for t in raw_tools]
    else:
        tools = []

    # sub_agents: None means leaf (cannot call anyone), list means can call those
    raw_sub = data.get("sub_agents")
    if raw_sub is None:
        sub_agents = None
    elif isinstance(raw_sub, str):
        sub_agents = [s.strip() for s in raw_sub.split(",") if s.strip()] or []
    elif isinstance(raw_sub, list):
        sub_agents = [str(s) for s in raw_sub]
    else:
        sub_agents = []

    try:
        return AgentConfig(
            name=name,
            display_name=display_name,
            description=description,
            graph_type=graph_type,
            service_type=service_type,
            model=model,
            tools=tools,
            sub_agents=sub_agents,
            max_context_tokens=max_context_tokens,
            system_prompt=body,
        )
    except Exception as e:
        logger.warning("Failed to validate AgentConfig for %s: %s — skipping.", filepath, e)
        return None


__all__ = ["parse_agent_file"]
