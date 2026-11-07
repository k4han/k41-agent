"""Helpers for assembling LangGraph system prompts."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

from agent.modules.skills import get_skills_catalog_xml
from agent.modules.tools import build_tool_catalogs, is_browser_tool
from agent.modules.workspaces import build_workspace_context

_WORKSPACE_STORAGE_TOOL_NAMES = frozenset(
    {"list_dir", "read", "write", "edit", "glob", "grep", "generate_image", "bash"}
)
BROWSER_REPORTING_RULES = (
    "When reporting on a website, state only what its page shows to a user: the accessibility snapshot, "
    "visible text or a screenshot. Data from script elements, page source or configuration objects does "
    "not show that a feature or option is available; if you mention it, say it came from page source and "
    "is unconfirmed. Do not add descriptions, capabilities or comparisons that you did not observe. "
    "If you could not confirm something, say so."
)


def with_browser_reporting_rules(prompt: str, tool_names: Iterable[str]) -> str:
    """Add the reporting rules for an agent that can read web pages."""
    if not any(is_browser_tool(name) for name in tool_names):
        return prompt
    return f"{prompt}\n\n{BROWSER_REPORTING_RULES}" if prompt.strip() else BROWSER_REPORTING_RULES


def _has_workspace_storage_tool(tools: Sequence[object] | None) -> bool:
    if not tools:
        return False
    return any(
        getattr(tool, "name", "") in _WORKSPACE_STORAGE_TOOL_NAMES
        for tool in tools
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


def get_system_default_variables(working_dir: str = "", workspace: str = "") -> dict[str, str]:
    from agent.modules.prompt_variables import get_execution_environment

    return {
        **get_execution_environment(),
        "workspace": workspace or working_dir or "",
        "working_dir": working_dir or "",
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
    scratchpad_path: str | None = None,
    sections: dict[str, str] | None = None,
) -> str:
    """Render agent/workspace context and collect catalogs for tool descriptions."""
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

    if sections is not None:
        skills_xml = skills_catalog_xml
        if skills_xml is None and _has_tool(tools, "skill"):
            skills_xml = get_skills_catalog_xml()
        sections.update(build_tool_catalogs(
            tools=tools, agent_name=agent_name, catalog=catalog,
            skills_catalog_xml=skills_xml or "<available_skills/>",
        ))

    system_prompt = with_browser_reporting_rules(system_prompt, (getattr(tool, "name", "") for tool in tools or ()))

    if _has_workspace_storage_tool(tools):
        system_prompt += "\n\n" + build_workspace_context(scratchpad_path)

    return system_prompt


__all__ = [
    "BROWSER_REPORTING_RULES",
    "build_llm_system_prompt",
    "replace_known_prompt_placeholders",
    "resolve_prompt_variables",
    "with_browser_reporting_rules",
]
