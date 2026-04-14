from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

import agent.modules.workflows.infrastructure.langgraph.nodes.tool as tool_node_module
from agent.modules.tools.infrastructure.langchain.agent_tools.call_agent import (
    call_agent,
)
from agent.modules.workflows.public import DEFAULT_WORKING_DIR
from agent.modules.tools.public import get_default_tool_names


@pytest.mark.asyncio
async def test_call_agent_inherits_parent_runtime_context(monkeypatch):
    captured: dict = {}

    class _FakeCatalog:
        def validate_call(self, caller_name: str, target_name: str) -> bool:
            captured["validate_call"] = (caller_name, target_name)
            return True

        def get_agent(self, name: str):
            if name != "child-agent":
                return None
            return SimpleNamespace(
                graph_type="react_agent",
                service_type="backend",
                max_context_tokens=1234,
                tools=["echo"],
            )

    class _FakeGraph:
        async def ainvoke(self, payload, *, config, context):
            captured["payload"] = payload
            captured["config"] = config
            captured["context"] = context
            return {"messages": [AIMessage(content="child complete")]}

    monkeypatch.setattr(
        "agent.modules.agents.public.get_catalog_service",
        lambda: _FakeCatalog(),
    )
    monkeypatch.setattr(
        "agent.modules.workflows.public.get_workflow_graph",
        lambda name: _FakeGraph(),
    )
    monkeypatch.setattr(
        "agent.modules.workflows.public.make_run_context",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(
        "agent.modules.workflows.public.make_run_config",
        lambda **kwargs: {"configurable": {"thread_id": kwargs["thread_id"]}},
    )

    runtime = SimpleNamespace(
        context={
            "agent_name": "parent-agent",
            "working_dir": "D:/repo",
        },
        config={
            "configurable": {
                "thread_id": "parent-thread",
            }
        },
    )

    result = await call_agent.coroutine(
        task="delegate this",
        sub_agent="child-agent",
        runtime=runtime,
    )

    assert result == "child complete"
    assert captured["validate_call"] == ("parent-agent", "child-agent")
    assert captured["payload"]["messages"][0].content == "delegate this"
    assert captured["context"]["working_dir"] == "D:/repo"
    assert captured["context"]["agent_name"] == "child-agent"
    assert captured["context"]["allowed_tool_names"] == ["echo"]
    assert captured["config"]["configurable"]["thread_id"].startswith(
        "parent-thread:sub:child-agent:"
    )


@pytest.mark.asyncio
async def test_call_agent_uses_canonical_default_working_dir(monkeypatch):
    captured: dict = {}

    class _FakeCatalog:
        def validate_call(self, caller_name: str, target_name: str) -> bool:
            return True

        def get_agent(self, name: str):
            if name != "child-agent":
                return None
            return SimpleNamespace(
                graph_type="react_agent",
                service_type="backend",
                max_context_tokens=1234,
                tools=["echo"],
            )

    class _FakeGraph:
        async def ainvoke(self, payload, *, config, context):
            captured["context"] = context
            return {"messages": [AIMessage(content="ok")]}

    monkeypatch.setattr(
        "agent.modules.agents.public.get_catalog_service",
        lambda: _FakeCatalog(),
    )
    monkeypatch.setattr(
        "agent.modules.workflows.public.get_workflow_graph",
        lambda name: _FakeGraph(),
    )
    monkeypatch.setattr(
        "agent.modules.workflows.public.make_run_context",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(
        "agent.modules.workflows.public.make_run_config",
        lambda **kwargs: {"configurable": {"thread_id": kwargs["thread_id"]}},
    )

    runtime = SimpleNamespace(
        context={
            "agent_name": "parent-agent",
        },
        config={
            "configurable": {
                "thread_id": "parent-thread",
            }
        },
    )

    result = await call_agent.coroutine(
        task="delegate this",
        sub_agent="child-agent",
        runtime=runtime,
    )

    assert result == "ok"
    assert captured["context"]["working_dir"] == DEFAULT_WORKING_DIR


@pytest.mark.asyncio
async def test_call_agent_omits_context_for_graph_without_context_schema(monkeypatch):
    captured: dict = {}

    class _FakeCatalog:
        def validate_call(self, caller_name: str, target_name: str) -> bool:
            return True

        def get_agent(self, name: str):
            if name != "child-agent":
                return None
            return SimpleNamespace(
                graph_type="research_chain",
                service_type="backend",
                max_context_tokens=1234,
                tools=["echo"],
            )

    class _FakeGraph:
        context_schema = None

        async def ainvoke(self, payload, *, config, **kwargs):
            captured["payload"] = payload
            captured["config"] = config
            captured["kwargs"] = kwargs
            return {"messages": [AIMessage(content="ok")]}

    monkeypatch.setattr(
        "agent.modules.agents.public.get_catalog_service",
        lambda: _FakeCatalog(),
    )
    monkeypatch.setattr(
        "agent.modules.workflows.public.get_workflow_graph",
        lambda name: _FakeGraph(),
    )
    monkeypatch.setattr(
        "agent.modules.workflows.public.make_run_context",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(
        "agent.modules.workflows.public.make_run_config",
        lambda **kwargs: {"configurable": {"thread_id": kwargs["thread_id"]}},
    )

    runtime = SimpleNamespace(
        context={"agent_name": "parent-agent", "working_dir": "D:/repo"},
        config={"configurable": {"thread_id": "parent-thread"}},
    )

    result = await call_agent.coroutine(
        task="delegate this",
        sub_agent="child-agent",
        runtime=runtime,
    )

    assert result == "ok"
    assert "context" not in captured["kwargs"]


@pytest.mark.asyncio
async def test_call_agent_extracts_last_text_from_structured_content(monkeypatch):
    class _FakeCatalog:
        def validate_call(self, caller_name: str, target_name: str) -> bool:
            return True

        def get_agent(self, name: str):
            if name != "child-agent":
                return None
            return SimpleNamespace(
                graph_type="react_agent",
                service_type="backend",
                max_context_tokens=1234,
                tools=["echo"],
            )

    class _FakeGraph:
        async def ainvoke(self, payload, *, config, context):
            return {
                "messages": [
                    AIMessage(
                        content=[
                            {"type": "thinking", "thinking": "internal"},
                            {"type": "text", "text": "child final response"},
                        ]
                    )
                ]
            }

    monkeypatch.setattr(
        "agent.modules.agents.public.get_catalog_service",
        lambda: _FakeCatalog(),
    )
    monkeypatch.setattr(
        "agent.modules.workflows.public.get_workflow_graph",
        lambda name: _FakeGraph(),
    )
    monkeypatch.setattr(
        "agent.modules.workflows.public.make_run_context",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(
        "agent.modules.workflows.public.make_run_config",
        lambda **kwargs: {"configurable": {"thread_id": kwargs["thread_id"]}},
    )

    runtime = SimpleNamespace(
        context={"agent_name": "parent-agent", "working_dir": "D:/repo"},
        config={"configurable": {"thread_id": "parent-thread"}},
    )

    result = await call_agent.coroutine(
        task="delegate this",
        sub_agent="child-agent",
        runtime=runtime,
    )

    assert result == "child final response"


@pytest.mark.asyncio
async def test_tool_node_resolves_runtime_allowed_tools(monkeypatch):
    captured: dict = {}

    class _FakeToolNode:
        def __init__(self, tools):
            captured["tool_names"] = [tool.name for tool in tools]

        async def ainvoke(self, state, *, config):
            captured["state"] = state
            captured["config"] = config
            return {"messages": []}

    monkeypatch.setattr(tool_node_module, "ToolNode", _FakeToolNode)
    monkeypatch.setattr(
        tool_node_module,
        "resolve_tools",
        lambda names: [SimpleNamespace(name=name) for name in names],
    )

    runtime = SimpleNamespace(
        context={"allowed_tool_names": ["echo", "call_agent"]},
    )
    config = {"configurable": {"thread_id": "thread-1"}}
    state = {"messages": ["ignored"]}

    result = await tool_node_module.tool_node(state, config=config, runtime=runtime)

    assert result == {"messages": []}
    assert captured["tool_names"] == ["echo", "call_agent"]
    assert captured["state"] == state
    assert captured["config"] == config


def test_default_tool_registry_includes_call_agent():
    assert "call_agent" in get_default_tool_names()
