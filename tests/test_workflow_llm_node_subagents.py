from types import SimpleNamespace

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
        tools=[SimpleNamespace(name="read_file")],
        catalog=_FakeCatalog(),
    )

    assert prompt == "Base prompt\nWorking directory: D:/repo"


def test_build_llm_system_prompt_injects_skills_section_when_skill_tool_exists(monkeypatch):
    monkeypatch.setattr(
        prompt_builders,
        "get_skills_catalog_xml",
        lambda: (
            "<available_skills><skill><name>sql-assistant</name>"
            "</skill></available_skills>"
        ),
    )

    prompt = prompt_builders.build_llm_system_prompt(
        system_prompt_template="Base prompt",
        working_dir="",
        agent_name="default",
        tools=[SimpleNamespace(name="skill")],
        catalog=_FakeCatalog(),
    )

    assert prompt_builders.SKILLS_DISCLOSURE_PROMPT in prompt
    assert "<available_skills>" in prompt


def test_build_llm_system_prompt_skips_skills_section_when_catalog_is_empty(monkeypatch):
    monkeypatch.setattr(
        prompt_builders,
        "get_skills_catalog_xml",
        lambda: "<available_skills/>",
    )

    prompt = prompt_builders.build_llm_system_prompt(
        system_prompt_template="Base prompt",
        working_dir="",
        agent_name="default",
        tools=[SimpleNamespace(name="skill")],
        catalog=_FakeCatalog(),
    )

    assert prompt == "Base prompt"


def test_build_llm_system_prompt_injects_callable_subagents_with_descriptions():
    prompt = prompt_builders.build_llm_system_prompt(
        system_prompt_template="Base prompt",
        working_dir="",
        agent_name="orchestrator",
        tools=[SimpleNamespace(name="call_agent")],
        catalog=_FakeCatalog(
            callable_agents=["research", "backend"],
            configs={
                "research": SimpleNamespace(
                    description="Research specialist for in-depth information gathering",
                ),
                "backend": SimpleNamespace(
                    description="Python/backend engineer assistant",
                ),
            },
        ),
    )

    assert prompt_builders.SUB_AGENT_DISCLOSURE_PROMPT in prompt
    assert "- research: Research specialist for in-depth information gathering" in prompt
    assert "- backend: Python/backend engineer assistant" in prompt


def test_build_llm_system_prompt_uses_fallback_for_missing_subagent_description():
    prompt = prompt_builders.build_llm_system_prompt(
        system_prompt_template="Base prompt",
        working_dir="",
        agent_name="orchestrator",
        tools=[SimpleNamespace(name="call_agent")],
        catalog=_FakeCatalog(
            callable_agents=["mystery-agent"],
            configs={"mystery-agent": SimpleNamespace(description="")},
        ),
    )

    assert "- mystery-agent: No description provided." in prompt


def test_build_llm_system_prompt_injects_empty_subagent_notice_when_none_are_callable():
    prompt = prompt_builders.build_llm_system_prompt(
        system_prompt_template="Base prompt",
        working_dir="",
        agent_name="solo",
        tools=[SimpleNamespace(name="call_agent")],
        catalog=_FakeCatalog(callable_agents=[]),
    )

    assert prompt_builders.SUB_AGENT_EMPTY_PROMPT in prompt


def test_build_llm_system_prompt_skips_subagent_section_when_call_agent_tool_is_not_bound():
    prompt = prompt_builders.build_llm_system_prompt(
        system_prompt_template="Base prompt",
        working_dir="",
        agent_name="writer",
        tools=[SimpleNamespace(name="read_file")],
        catalog=_FakeCatalog(
            callable_agents=["research"],
            configs={
                "research": SimpleNamespace(
                    description="Research specialist for in-depth information gathering",
                )
            },
        ),
    )

    assert prompt_builders.SUB_AGENT_DISCLOSURE_PROMPT not in prompt
    assert prompt_builders.SUB_AGENT_EMPTY_PROMPT not in prompt
    assert "- research:" not in prompt


def test_build_llm_system_prompt_appends_subagents_before_skills(monkeypatch):
    monkeypatch.setattr(
        prompt_builders,
        "get_skills_catalog_xml",
        lambda: "<available_skills><skill><name>x</name></skill></available_skills>",
    )

    prompt = prompt_builders.build_llm_system_prompt(
        system_prompt_template="Base prompt",
        working_dir="",
        agent_name="orchestrator",
        tools=[SimpleNamespace(name="call_agent"), SimpleNamespace(name="skill")],
        catalog=_FakeCatalog(
            callable_agents=["research"],
            configs={
                "research": SimpleNamespace(
                    description="Research specialist for in-depth information gathering",
                )
            },
        ),
    )

    assert prompt.index(prompt_builders.SUB_AGENT_DISCLOSURE_PROMPT) < prompt.index(
        prompt_builders.SKILLS_DISCLOSURE_PROMPT
    )
