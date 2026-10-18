"""Request-scoped discovery data in tool descriptions."""

from collections.abc import Sequence
from copy import copy
from typing import Any


def build_tool_catalogs(
    *,
    tools: Sequence[object] | None,
    agent_name: str,
    catalog: Any,
    skills_catalog_xml: str,
) -> dict[str, str]:
    names = {getattr(tool, "name", "") for tool in tools or ()}
    sections: dict[str, str] = {}
    if "skill" in names and skills_catalog_xml.strip() not in {"", "<available_skills/>"}:
        sections["skills"] = skills_catalog_xml.strip()
    if "delegate_task" in names:
        get_callable = getattr(catalog, "get_callable_agents", None)
        agents = get_callable(agent_name) if callable(get_callable) else []
        lines = []
        for name in agents or ():
            config = catalog.get_agent(name)
            description = str(getattr(config, "description", "") or "").strip()
            lines.append(f"- {name}: {description or 'No description provided.'}")
        if lines:
            sections["subagents"] = "<callable_agents>\n" + "\n".join(lines) + "\n</callable_agents>"
    return sections


def bind_tool_catalogs(tools: Sequence[Any], sections: dict[str, str], *, keep_skill: bool = False) -> list[Any]:
    """Copy tools before adding catalogs; never mutate the shared registry."""
    result = []
    for tool in tools:
        key = {"skill": "skills", "delegate_task": "subagents"}.get(tool.name)
        section = sections.get(key, "") if key else ""
        if key and not section and not (tool.name == "skill" and keep_skill):
            continue
        if section:
            tool = copy(tool)
            tool.description = f"{tool.description}\n\n{section}"
        result.append(tool)
    return result
