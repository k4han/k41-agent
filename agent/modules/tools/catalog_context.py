"""Compact, request-scoped discovery data in tool descriptions."""

import logging
from collections.abc import Sequence
from copy import copy
from typing import Any
from xml.etree import ElementTree

logger = logging.getLogger(__name__)


def _catalog_line(name: str, description: str) -> str:
    description = " ".join(description.split())
    return f"- {name}: {description or 'No description provided.'}"


def _compact_skill_catalog(skills_catalog_xml: str) -> str:
    """Keep selection metadata; resource paths are supplied when a skill loads."""
    if not skills_catalog_xml.strip():
        return ""
    try:
        root = ElementTree.fromstring(skills_catalog_xml)
    except ElementTree.ParseError:
        logger.warning("Unable to parse the skills discovery catalog")
        return ""
    lines = [
        _catalog_line(name, skill.findtext("description", ""))
        for skill in root.findall("skill")
        if (name := skill.findtext("name", "").strip())
    ]
    return "Available skills:\n" + "\n".join(lines) if lines else ""


def build_tool_catalogs(
    *,
    tools: Sequence[object] | None,
    agent_name: str,
    catalog: Any,
    skills_catalog_xml: str,
) -> dict[str, str]:
    names = {getattr(tool, "name", "") for tool in tools or ()}
    sections: dict[str, str] = {}
    if "skill" in names:
        skills = _compact_skill_catalog(skills_catalog_xml)
        if skills:
            sections["skills"] = skills
    if names.intersection({"delegate_task", "start_async_task"}):
        get_callable = getattr(catalog, "get_callable_agents", None)
        agents = get_callable(agent_name) if callable(get_callable) else []
        lines = []
        for name in agents or ():
            config = catalog.get_agent(name)
            description = str(getattr(config, "description", "") or "")
            lines.append(_catalog_line(name, description))
        if lines:
            sections["subagents"] = "Callable agents:\n" + "\n".join(lines)
    return sections


def bind_tool_catalogs(tools: Sequence[Any], sections: dict[str, str], *, keep_skill: bool = False) -> list[Any]:
    """Copy tools before adding catalogs; never mutate the shared registry."""
    result = []
    for tool in tools:
        key = {"skill": "skills", "delegate_task": "subagents", "start_async_task": "subagents"}.get(tool.name)
        section = sections.get(key, "") if key else ""
        if key and not section and not (tool.name == "skill" and keep_skill):
            continue
        if section:
            tool = copy(tool)
            tool.description = f"{tool.description}\n\n{section}"
        result.append(tool)
    return result
