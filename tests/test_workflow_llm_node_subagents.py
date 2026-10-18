from types import SimpleNamespace

import pytest
from langchain_core.tools import StructuredTool
from agent.modules.tools.catalog_context import bind_tool_catalogs

import agent.modules.workflows.prompt_builders as prompt_builders


class _FakeCatalog:
    def __init__(self, callable_agents=None, configs=None):
        self._callable_agents = list(callable_agents or [])
        self._configs = configs or {}

    def get_callable_agents(self, for_agent_name: str):
        self.requested_agent_name = for_agent_name
        return list(self._callable_agents)

    def get_agent(self, name: str):
        return self._configs.get(name)


def test_build_llm_system_prompt_formats_working_dir_without_extra_sections():
    prompt = prompt_builders.build_llm_system_prompt(
        system_prompt_template="Base prompt\nWorking directory: {working_dir}",
        working_dir="D:/repo",
        agent_name="default",
        tools=[],
        catalog=_FakeCatalog(),
    )

    assert prompt == "Base prompt\nWorking directory: D:/repo"


def test_build_llm_system_prompt_resolves_prompt_variables_before_runtime_placeholders():
    prompt = prompt_builders.build_llm_system_prompt(
        system_prompt_template=(
            "Base prompt\n"
            "{{common_rules}}\n"
            "{{missing_rules}}\n"
            "Working directory: {working_dir}"
        ),
        working_dir="D:/repo",
        agent_name="default",
        tools=[SimpleNamespace(name="read")],
        catalog=_FakeCatalog(),
        prompt_variables={"common_rules": "Use project conventions."},
    )

    assert "Use project conventions." in prompt
    assert "{{missing_rules}}" in prompt
    assert "Working directory: D:/repo" in prompt


def test_known_prompt_placeholders_do_not_change_double_brace_variables():
    prompt = prompt_builders.build_llm_system_prompt(
        system_prompt_template="{{working_dir}}\n{working_dir}",
        working_dir="D:/repo",
        agent_name="default",
        tools=[],
        catalog=_FakeCatalog(),
        prompt_variables={},
    )

    assert prompt == "D:/repo\nD:/repo"


def test_build_llm_system_prompt_resolves_workspace_and_working_dir_independently():
    """`{{workspace}}` keeps the friendly label, `{{working_dir}}` carries the real path."""
    prompt = prompt_builders.build_llm_system_prompt(
        system_prompt_template=(
            "Workspace: {{workspace}}\n"
            "Cwd: {{working_dir}}"
        ),
        working_dir="/workspace/facebook/react",
        workspace="facebook/react",
        agent_name="default",
        tools=[SimpleNamespace(name="read")],
        catalog=_FakeCatalog(),
    )

    assert "Workspace: facebook/react" in prompt
    assert "Cwd: /workspace/facebook/react" in prompt


def _tool(name):
    return StructuredTool.from_function(lambda query="": query, name=name, description=f"Use {name}.")


def test_catalogs_are_bound_to_request_scoped_tool_copies():
    tools = [_tool("delegate_task"), _tool("skill")]
    sections = {}
    prompt = prompt_builders.build_llm_system_prompt(
        system_prompt_template="Base prompt", working_dir="", agent_name="orchestrator",
        tools=tools, catalog=_FakeCatalog(["research"], {"research": SimpleNamespace(description="Find sources.")}),
        skills_catalog_xml="<available_skills><skill><name>sql</name></skill></available_skills>",
        sections=sections,
    )
    bound = bind_tool_catalogs(tools, sections)
    assert prompt == "Base prompt"
    assert "- research: Find sources." in bound[0].description
    assert "<name>sql</name>" in bound[1].description
    assert "research" not in tools[0].description
    assert "<available_skills>" not in tools[1].description
    other = bind_tool_catalogs(tools, {"subagents": "- coder: Write code."})
    assert [tool.name for tool in other] == ["delegate_task"]
    assert "research" not in other[0].description
    assert "coder" in other[0].description


@pytest.mark.parametrize("keep_skill,expected", [(False, []), (True, ["skill"])])
def test_empty_catalogs_disable_unusable_tools_but_allow_unloading_active_skills(keep_skill, expected):
    result = bind_tool_catalogs([_tool("skill"), _tool("delegate_task")], {}, keep_skill=keep_skill)
    assert [tool.name for tool in result] == expected


def test_catalogs_use_only_callable_agents_and_preserve_missing_description_fallback():
    sections = {}
    prompt_builders.build_llm_system_prompt(
        system_prompt_template="Base", working_dir="", agent_name="caller",
        tools=[_tool("delegate_task")], catalog=_FakeCatalog(["mystery"], {}), sections=sections,
    )
    assert "- mystery: No description provided." in sections["subagents"]
    assert "skills" not in sections


def test_catalogs_are_absent_when_discovery_tools_are_not_enabled():
    sections = {}
    prompt_builders.build_llm_system_prompt(
        system_prompt_template="Base", working_dir="", agent_name="caller", tools=[],
        catalog=_FakeCatalog(["research"]), sections=sections,
        skills_catalog_xml="<available_skills><skill>Hidden</skill></available_skills>",
    )
    assert sections == {}
