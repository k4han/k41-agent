"""Helpers for assembling LangGraph system prompts."""

from __future__ import annotations

import re
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

WRITE_TODOS_PROMPT = (
    "The write_todos tool can manage a structured todo list for complex, "
    "multi-step work.\n"
    "Use it when tracking progress is useful, especially for tasks with three "
    "or more meaningful steps, explicit todo requests, or plans that may change "
    "as you learn more.\n"
    "Do not use it for trivial or purely conversational requests. When you use "
    "it, keep exactly the current useful todo list, update statuses as soon as "
    "work changes, and do not call write_todos multiple times in parallel.\n"
    "Valid statuses are pending, in_progress, and completed."
)

_PROMPT_VARIABLE_RE = re.compile(r"\{\{([A-Za-z][A-Za-z0-9_-]{0,63})\}\}")


def _has_tool(tools: Sequence[object] | None, tool_name: str) -> bool:
    return any(getattr(tool, "name", "") == tool_name for tool in tools or ())


def resolve_prompt_variables(
    template: str,
    prompt_variables: dict[str, str] | None,
) -> str:
    """Replace {{name}} placeholders with configured prompt variable values."""
    variables = prompt_variables or {}

    def replace_match(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            return match.group(0)
        return variables[name]

    return _PROMPT_VARIABLE_RE.sub(replace_match, template)


def replace_known_prompt_placeholders(
    template: str,
    values: dict[str, str],
) -> str:
    """Replace known single-brace placeholders without touching {{variables}}."""
    resolved = template
    for name, value in values.items():
        pattern = re.compile(rf"(?<!\{{)\{{{re.escape(name)}\}}(?!\}})")
        resolved = pattern.sub(lambda _match, v=value: v, resolved)
    return resolved


def _build_skills_prompt_section(catalog_xml: str | None = None) -> str:
    catalog_xml = (catalog_xml if catalog_xml is not None else get_skills_catalog_xml()).strip()
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


def get_system_default_variables(working_dir: str = "", workspace: str = "") -> dict[str, str]:
    import sys
    import getpass

    from agent.shared.timezone import display_now

    os_name = sys.platform
    if os_name == "win32":
        os_name = "windows"
    elif os_name == "darwin":
        os_name = "macos"

    try:
        username = getpass.getuser()
    except Exception:
        username = "user"

    current_time_str = display_now().strftime("%Y-%m-%d %H:%M:%S %Z (UTC%z)")

    return {
        "current_time": current_time_str,
        "operating_system": os_name,
        "workspace": workspace or working_dir or "",
        "working_dir": working_dir or "",
        "user_name": username,
    }


def build_llm_system_prompt(
    *,
    system_prompt_template: str,
    working_dir: str,
    workspace: str = "",
    agent_name: str,
    tools: Sequence[object] | None,
    catalog: Any,
    prompt_variables: dict[str, str] | None = None,
    skills_catalog_xml: str | None = None,
) -> str:
    """Build the final system prompt for llm_node from runtime state."""
    system_defaults = get_system_default_variables(
        working_dir=working_dir,
        workspace=workspace or working_dir,
    )
    merged_vars = {**system_defaults, **(prompt_variables or {})}

    system_prompt = resolve_prompt_variables(
        system_prompt_template,
        merged_vars,
    )
    system_prompt = replace_known_prompt_placeholders(
        system_prompt,
        {
            "working_dir": working_dir,
            "workspace": workspace or working_dir,
        },
    )

    if _has_tool(tools, "call_agent"):
        system_prompt = (
            f"{system_prompt}{_build_sub_agents_prompt_section(agent_name, catalog)}"
        )

    if _has_tool(tools, "write_todos"):
        system_prompt = f"{system_prompt}\n\n{WRITE_TODOS_PROMPT}"

    if _has_tool(tools, "skill"):
        system_prompt = f"{system_prompt}{_build_skills_prompt_section(skills_catalog_xml)}"

    return system_prompt


__all__ = [
    "SKILLS_DISCLOSURE_PROMPT",
    "SUB_AGENT_DISCLOSURE_PROMPT",
    "SUB_AGENT_EMPTY_PROMPT",
    "WRITE_TODOS_PROMPT",
    "build_llm_system_prompt",
    "replace_known_prompt_placeholders",
    "resolve_prompt_variables",
]
