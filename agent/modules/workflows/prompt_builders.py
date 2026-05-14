"""Helpers for assembling LangGraph system prompts."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent.modules.skills import get_skills_catalog_xml

SKILLS_DISCLOSURE_PROMPT = (
    "The following skills provide specialized instructions for specific tasks.\n"
    "When a task matches a skill description, call the skill tool with the skill "
    "name to load full instructions before proceeding."
)

SUB_AGENT_DISCLOSURE_PROMPT = (
    "The call_agent tool can delegate work to the following specialized "
    "sub-agents.\n"
    "Use the exact sub_agent name when delegating:"
)

SUB_AGENT_EMPTY_PROMPT = (
    "The call_agent tool is available, but this agent has no callable "
    "sub-agents configured.\n"
    "Do not call call_agent unless a callable sub-agent is listed."
)


def _has_tool(tools: Sequence[object] | None, tool_name: str) -> bool:
    return any(getattr(tool, "name", "") == tool_name for tool in tools or ())


def _build_skills_prompt_section() -> str:
    catalog_xml = get_skills_catalog_xml().strip()
    if catalog_xml == "<available_skills/>":
        return ""
    return f"\n\n{SKILLS_DISCLOSURE_PROMPT}\n{catalog_xml}"


def _build_sub_agents_prompt_section(agent_name: str, catalog: Any) -> str:
    get_callable_agents = getattr(catalog, "get_callable_agents", None)
    if not callable(get_callable_agents):
        return ""

    callable_agents = list(get_callable_agents(agent_name) or [])
    if not callable_agents:
        return f"\n\n{SUB_AGENT_EMPTY_PROMPT}"

    lines = [SUB_AGENT_DISCLOSURE_PROMPT]
    for sub_agent_name in callable_agents:
        sub_agent_config = catalog.get_agent(sub_agent_name)
        description = ""
        if sub_agent_config is not None:
            description = str(getattr(sub_agent_config, "description", "") or "").strip()
        if not description:
            description = "No description provided."
        lines.append(f"- {sub_agent_name}: {description}")
    section = "\n".join(lines)
    return f"\n\n{section}"


def build_llm_system_prompt(
    *,
    system_prompt_template: str,
    working_dir: str,
    agent_name: str,
    tools: Sequence[object] | None,
    catalog: Any,
) -> str:
    """Build the final system prompt for llm_node from runtime state."""
    system_prompt = system_prompt_template
    if working_dir and "{working_dir}" in system_prompt:
        system_prompt = system_prompt.format(working_dir=working_dir)

    if _has_tool(tools, "call_agent"):
        system_prompt = (
            f"{system_prompt}{_build_sub_agents_prompt_section(agent_name, catalog)}"
        )

    if _has_tool(tools, "skill"):
        system_prompt = f"{system_prompt}{_build_skills_prompt_section()}"

    return system_prompt


__all__ = [
    "SKILLS_DISCLOSURE_PROMPT",
    "SUB_AGENT_DISCLOSURE_PROMPT",
    "SUB_AGENT_EMPTY_PROMPT",
    "build_llm_system_prompt",
]
