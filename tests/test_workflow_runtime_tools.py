from types import SimpleNamespace

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode as LangGraphToolNode
from pydantic import ValidationError
import pytest

import agent.modules.workflows.nodes.tool as tool_node_module
import agent.modules.workflows.prompt_builders as prompt_builders
from agent.modules.tools import get_default_tool_names
from agent.modules.tools.langchain.agent_tools.call_agent import (
    call_agent,
)
from agent.modules.tools.langchain.utility_tools.write_todos import write_todos
from agent.modules.workflows.state.base import BaseState


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

    async def _fake_run_agent_full(**kwargs):
        captured["run_kwargs"] = kwargs
        return "child complete"

    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: _FakeCatalog(),
    )
    monkeypatch.setattr(
        "agent.modules.agent_runtime.run_agent_full",
        _fake_run_agent_full,
    )

    runtime = SimpleNamespace(
        context={
            "agent_name": "parent-agent",
            "working_dir": "D:/repo",
            "provider": "openai-main",
            "model": "parent-model",
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
    run_kwargs = captured["run_kwargs"]
    assert run_kwargs["user_input"] == "delegate this"
    assert run_kwargs["agent_name"] == "child-agent"
    assert run_kwargs["workspace"] == "D:/repo"
    assert run_kwargs["provider"] == "openai-main"
    assert run_kwargs["model"] == "parent-model"
    assert run_kwargs["thread_id"].startswith("parent-thread:sub:child-agent:")


@pytest.mark.asyncio
async def test_call_agent_passes_none_workspace_when_unset(monkeypatch):
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

    async def _fake_run_agent_full(**kwargs):
        captured["run_kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: _FakeCatalog(),
    )
    monkeypatch.setattr(
        "agent.modules.agent_runtime.run_agent_full",
        _fake_run_agent_full,
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
    assert captured["run_kwargs"]["workspace"] is None


@pytest.mark.asyncio
async def test_call_agent_returns_empty_response_placeholder(monkeypatch):
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

    async def _fake_run_agent_full(**kwargs):
        return ""

    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: _FakeCatalog(),
    )
    monkeypatch.setattr(
        "agent.modules.agent_runtime.run_agent_full",
        _fake_run_agent_full,
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

    assert result == "(empty response)"


@pytest.mark.asyncio
async def test_call_agent_blocks_when_validate_call_fails(monkeypatch):
    class _FakeCatalog:
        def validate_call(self, caller_name: str, target_name: str) -> bool:
            return False

        def get_agent(self, name: str):
            return SimpleNamespace(
                graph_type="react_agent",
                service_type="backend",
                max_context_tokens=1234,
                tools=["echo"],
            )

    invoked = False

    async def _fake_run_agent_full(**kwargs):
        nonlocal invoked
        invoked = True
        return "should not run"

    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: _FakeCatalog(),
    )
    monkeypatch.setattr(
        "agent.modules.agent_runtime.run_agent_full",
        _fake_run_agent_full,
    )

    runtime = SimpleNamespace(
        context={"agent_name": "parent-agent"},
        config={"configurable": {"thread_id": "parent-thread"}},
    )

    result = await call_agent.coroutine(
        task="delegate this",
        sub_agent="child-agent",
        runtime=runtime,
    )

    assert result == "[error] permission_denied: not allowed to call agent 'child-agent'."
    assert invoked is False


@pytest.mark.asyncio
async def test_call_agent_reports_runner_failure(monkeypatch):
    class _FakeCatalog:
        def validate_call(self, caller_name: str, target_name: str) -> bool:
            return True

        def get_agent(self, name: str):
            return SimpleNamespace(
                graph_type="react_agent",
                service_type="backend",
                max_context_tokens=1234,
                tools=["echo"],
            )

    async def _fake_run_agent_full(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: _FakeCatalog(),
    )
    monkeypatch.setattr(
        "agent.modules.agent_runtime.run_agent_full",
        _fake_run_agent_full,
    )

    runtime = SimpleNamespace(
        context={"agent_name": "parent-agent"},
        config={"configurable": {"thread_id": "parent-thread"}},
    )

    result = await call_agent.coroutine(
        task="delegate this",
        sub_agent="child-agent",
        runtime=runtime,
    )

    assert result == "[error] upstream: sub-agent 'child-agent' failed: boom"


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

    async def _fake_resolve(self, agent_name, *, override_tool_names=None):
        names = list(override_tool_names) if override_tool_names else []
        return [SimpleNamespace(name=name) for name in names]

    monkeypatch.setattr(
        tool_node_module.ToolResolver,
        "aresolve_for_agent",
        _fake_resolve,
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


def test_default_tool_registry_includes_runtime_tools():
    assert "call_agent" in get_default_tool_names()
    assert "write_todos" in get_default_tool_names()


def test_write_todos_tool_validates_status() -> None:
    assert write_todos.args_schema is not None

    with pytest.raises(ValidationError):
        write_todos.args_schema.model_validate(
            {
                "todos": [
                    {
                        "content": "Review implementation",
                        "status": "blocked",
                    }
                ]
            }
        )


def test_write_todos_tool_updates_graph_state() -> None:
    todos = [{"content": "Review implementation", "status": "in_progress"}]

    def _request_todos(_state):
        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {"todos": todos},
                            "id": "call-1",
                        }
                    ],
                )
            ]
        }

    graph = StateGraph(BaseState)
    graph.add_node("request_todos", _request_todos)
    graph.add_node("tools", LangGraphToolNode([write_todos]))
    graph.add_edge(START, "request_todos")
    graph.add_edge("request_todos", "tools")
    graph.add_edge("tools", END)

    result = graph.compile().invoke({"messages": []})

    assert result["todos"] == todos
    assert result["messages"][-1].name == "write_todos"


def test_build_llm_system_prompt_injects_write_todos_section_only_when_bound() -> None:
    prompt = prompt_builders.build_llm_system_prompt(
        system_prompt_template="Base prompt",
        working_dir="",
        agent_name="default",
        tools=[SimpleNamespace(name="write_todos")],
        catalog=SimpleNamespace(),
    )

    without_tool = prompt_builders.build_llm_system_prompt(
        system_prompt_template="Base prompt",
        working_dir="",
        agent_name="default",
        tools=[SimpleNamespace(name="read_file")],
        catalog=SimpleNamespace(),
    )

    assert prompt_builders.WRITE_TODOS_PROMPT in prompt
    assert prompt_builders.WRITE_TODOS_PROMPT not in without_tool


@pytest.mark.asyncio
async def test_tool_node_rejects_parallel_write_todos_calls():
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_todos",
                        "args": {"todos": [{"content": "One", "status": "pending"}]},
                        "id": "call-1",
                    },
                    {
                        "name": "write_todos",
                        "args": {"todos": [{"content": "Two", "status": "pending"}]},
                        "id": "call-2",
                    },
                ],
            )
        ]
    }

    result = await tool_node_module.tool_node(
        state,
        config={"configurable": {"thread_id": "thread-1"}},
        runtime=SimpleNamespace(context={}),
    )

    messages = result["messages"]
    assert [message.tool_call_id for message in messages] == ["call-1", "call-2"]
    assert [message.status for message in messages] == ["error", "error"]
    assert "should not be called multiple times" in messages[0].content
