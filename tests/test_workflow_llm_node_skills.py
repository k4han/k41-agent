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
        return _FakeChatModel(captured)

    return _factory


def test_make_llm_node_injects_skills_catalog_when_skill_tool_exists(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        llm_node_module,
        "get_chat_model",
        _fake_chat_model_factory(captured),
    )
    monkeypatch.setattr(
        llm_node_module,
        "get_skills_catalog_xml",
        lambda: (
            "<available_skills><skill><name>sql-assistant</name>"
            "</skill></available_skills>"
        ),
    )

    node = llm_node_module.make_llm_node(
        tools=[SimpleNamespace(name="skill")],
        system_prompts={
            "backend": (
                "Custom backend assistant.\n"
                "Working directory: {working_dir}"
            )
        },
    )

    result = node(
        {"messages": [HumanMessage(content="help me")]},
        {
            "configurable": {
                "service_type": "backend",
                "working_dir": "D:/repo",
            }
        },
    )

    assert result["messages"][0].content == "ok"
    system_message = captured["messages"][0]
    assert isinstance(system_message, SystemMessage)
    assert "Custom backend assistant." in system_message.content
    assert "D:/repo" in system_message.content
    assert "<available_skills>" in system_message.content
    assert "call the skill tool" in system_message.content


def test_make_llm_node_skips_skills_catalog_when_catalog_is_empty(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        llm_node_module,
        "get_chat_model",
        _fake_chat_model_factory(captured),
    )
    monkeypatch.setattr(
        llm_node_module,
        "get_skills_catalog_xml",
        lambda: "<available_skills/>",
    )

    node = llm_node_module.make_llm_node(
        tools=[SimpleNamespace(name="skill")],
        system_prompts={
            "backend": (
                "Custom backend assistant.\n"
                "Working directory: {working_dir}"
            )
        },
    )

    node(
        {"messages": [HumanMessage(content="help me")]},
        {
            "configurable": {
                "service_type": "backend",
                "working_dir": "D:/repo",
            }
        },
    )

    system_message = captured["messages"][0]
    assert isinstance(system_message, SystemMessage)
    assert "<available_skills" not in system_message.content
    assert "call the skill tool" not in system_message.content


def test_make_llm_node_skips_catalog_when_skill_tool_not_registered(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        llm_node_module,
        "get_chat_model",
        _fake_chat_model_factory(captured),
    )
    monkeypatch.setattr(
        llm_node_module,
        "get_skills_catalog_xml",
        lambda: "<available_skills><skill><name>x</name></skill></available_skills>",
    )

    node = llm_node_module.make_llm_node(
        tools=[SimpleNamespace(name="echo")],
    )

    node(
        {"messages": [HumanMessage(content="hello")]},
        {"configurable": {"service_type": "default", "working_dir": "."}},
    )

    system_message = captured["messages"][0]
    assert isinstance(system_message, SystemMessage)
    assert "<available_skills" not in system_message.content
