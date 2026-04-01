from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import agent.modules.workflows.infrastructure.langgraph.nodes.llm as llm_node_module


class _FakeChatModel:
    def __init__(self, captured: dict):
        self._captured = captured

    def bind_tools(self, tools):
        self._captured["tools"] = tools
        return self

    def invoke(self, messages):
        self._captured["messages"] = messages
        return AIMessage(content="ok")


def _fake_chat_model_factory(captured: dict):
    def _factory(model: str):
        captured["model"] = model
        return _FakeChatModel(captured)

    return _factory


def test_llm_node_injects_callable_subagents_into_system_prompt(monkeypatch):
    captured: dict = {}
    configs = {
        "orchestrator": SimpleNamespace(
            model="devstral-2512",
            system_prompt="You are an orchestrator.\nWorking directory: {working_dir}",
            tools=["call_agent", "read_file"],
            description="",
        ),
        "research": SimpleNamespace(
            description="Research specialist for in-depth information gathering",
        ),
        "backend": SimpleNamespace(
            description="Python/backend engineer assistant",
        ),
    }

    class _FakeCatalog:
        def get_agent(self, name: str):
            return configs.get(name)

        def get_callable_agents(self, for_agent_name: str):
            assert for_agent_name == "orchestrator"
            return ["research", "backend"]

    monkeypatch.setattr(
        llm_node_module,
        "get_chat_model",
        _fake_chat_model_factory(captured),
    )
    monkeypatch.setattr(
        llm_node_module,
        "_resolve_tools",
        lambda names: [SimpleNamespace(name=name) for name in names],
    )
    monkeypatch.setattr(
        "agent.modules.agents.public.get_catalog_service",
        lambda: _FakeCatalog(),
    )

    result = llm_node_module.llm_node(
        {"messages": [HumanMessage(content="delegate this task")]},
        SimpleNamespace(
            context={
                "agent_name": "orchestrator",
                "working_dir": "D:/repo",
            }
        ),
    )

    assert result["messages"][0].content == "ok"
    system_message = captured["messages"][0]
    assert isinstance(system_message, SystemMessage)
    assert "You are an orchestrator." in system_message.content
    assert "D:/repo" in system_message.content
    assert llm_node_module.SUB_AGENT_DISCLOSURE_PROMPT in system_message.content
    assert (
        "- research: Research specialist for in-depth information gathering"
        in system_message.content
    )
    assert "- backend: Python/backend engineer assistant" in system_message.content


def test_llm_node_injects_empty_subagent_notice_when_none_are_callable(monkeypatch):
    captured: dict = {}
    configs = {
        "solo": SimpleNamespace(
            model="devstral-2512",
            system_prompt="You are a solo agent.\nWorking directory: {working_dir}",
            tools=["call_agent"],
            description="",
        ),
    }

    class _FakeCatalog:
        def get_agent(self, name: str):
            return configs.get(name)

        def get_callable_agents(self, for_agent_name: str):
            assert for_agent_name == "solo"
            return []

    monkeypatch.setattr(
        llm_node_module,
        "get_chat_model",
        _fake_chat_model_factory(captured),
    )
    monkeypatch.setattr(
        llm_node_module,
        "_resolve_tools",
        lambda names: [SimpleNamespace(name=name) for name in names],
    )
    monkeypatch.setattr(
        "agent.modules.agents.public.get_catalog_service",
        lambda: _FakeCatalog(),
    )

    llm_node_module.llm_node(
        {"messages": [HumanMessage(content="work alone")]},
        SimpleNamespace(
            context={
                "agent_name": "solo",
                "working_dir": "D:/repo",
            }
        ),
    )

    system_message = captured["messages"][0]
    assert isinstance(system_message, SystemMessage)
    assert llm_node_module.SUB_AGENT_EMPTY_PROMPT in system_message.content


def test_llm_node_skips_subagent_section_when_call_agent_tool_is_not_bound(monkeypatch):
    captured: dict = {}
    configs = {
        "writer": SimpleNamespace(
            model="devstral-2512",
            system_prompt="You are a writer.\nWorking directory: {working_dir}",
            tools=["read_file"],
            description="",
        ),
        "research": SimpleNamespace(
            description="Research specialist for in-depth information gathering",
        ),
    }

    class _FakeCatalog:
        def get_agent(self, name: str):
            return configs.get(name)

        def get_callable_agents(self, for_agent_name: str):
            assert for_agent_name == "writer"
            return ["research"]

    monkeypatch.setattr(
        llm_node_module,
        "get_chat_model",
        _fake_chat_model_factory(captured),
    )
    monkeypatch.setattr(
        llm_node_module,
        "_resolve_tools",
        lambda names: [SimpleNamespace(name=name) for name in names],
    )
    monkeypatch.setattr(
        "agent.modules.agents.public.get_catalog_service",
        lambda: _FakeCatalog(),
    )

    llm_node_module.llm_node(
        {"messages": [HumanMessage(content="write this")]},
        SimpleNamespace(
            context={
                "agent_name": "writer",
                "working_dir": "D:/repo",
            }
        ),
    )

    system_message = captured["messages"][0]
    assert isinstance(system_message, SystemMessage)
    assert llm_node_module.SUB_AGENT_DISCLOSURE_PROMPT not in system_message.content
    assert llm_node_module.SUB_AGENT_EMPTY_PROMPT not in system_message.content
    assert "- research:" not in system_message.content
