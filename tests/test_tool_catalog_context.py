"""Discovery catalogs retain selection metadata without redundant XML or paths."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import StructuredTool

from agent.modules.skills.models import SkillSummary
from agent.modules.skills.rendering import skill_catalog_xml
from agent.modules.tools.catalog_context import bind_tool_catalogs, build_tool_catalogs


def _tool(name):
    return StructuredTool.from_function(lambda: "", name=name, description=f"Use {name}.")


def test_compact_catalog_preserves_names_and_full_descriptions_without_paths():
    description = "Review code & check <types>.\n\n  " + "Keep all selection details. " * 50
    xml = skill_catalog_xml([
        SkillSummary("review", description, Path("/private/skills/review")),
        SkillSummary("sql", "", Path("/private/skills/sql")),
    ])
    catalog = SimpleNamespace(
        get_callable_agents=lambda caller: ["researcher"],
        get_agent=lambda name: SimpleNamespace(description="Find sources.\n\n  Verify claims."),
    )
    sections = build_tool_catalogs(
        tools=[_tool("skill"), _tool("delegate_task")], agent_name="caller",
        catalog=catalog, skills_catalog_xml=xml,
    )

    assert sections["skills"].splitlines() == [
        "Available skills:",
        "- review: " + " ".join(description.split()),
        "- sql: No description provided.",
    ]
    assert "/private" not in sections["skills"]
    assert "<location>" not in sections["skills"]
    assert sections["subagents"].splitlines() == [
        "Callable agents:", "- researcher: Find sources. Verify claims.",
    ]


def test_compact_skill_catalog_substantially_reduces_bound_tool_tokens():
    xml = skill_catalog_xml([
        SkillSummary(f"skill-{index}", f"Handle task {index} with its specialized workflow.",
                     Path(f"/workspace/project/.agents/skills/skill-{index}"))
        for index in range(25)
    ])
    tools = [_tool("skill")]
    sections = build_tool_catalogs(
        tools=tools, agent_name="caller", catalog=None, skills_catalog_xml=xml,
    )
    original = bind_tool_catalogs(tools, {"skills": xml})
    compact = bind_tool_catalogs(tools, sections)

    assert count_tokens_approximately([], tools=compact) < count_tokens_approximately([], tools=original) / 2
    for index in range(25):
        assert f"- skill-{index}: Handle task {index} with its specialized workflow." in compact[0].description
    assert tools[0].description == "Use skill."


@pytest.mark.parametrize("xml", ["", "<available_skills/>", "<available_skills />", "<available_skills>"])
def test_empty_or_invalid_skill_catalog_does_not_expose_an_unusable_tool(xml):
    tools = [_tool("skill")]
    sections = build_tool_catalogs(
        tools=tools, agent_name="caller", catalog=None, skills_catalog_xml=xml,
    )

    assert bind_tool_catalogs(tools, sections) == []
    assert bind_tool_catalogs(tools, sections, keep_skill=True) == tools
