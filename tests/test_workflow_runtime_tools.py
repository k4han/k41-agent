from types import SimpleNamespace

import pytest

import agent.modules.workflows.nodes.tool as tool_node_module
from agent.modules.tools import get_default_tool_names
from agent.modules.tools.langchain.agent_tools.call_agent import (
    call_agent,
)


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
        "agent.modules.agent_runtime.runner.run_agent_full",
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
        "agent.modules.agent_runtime.runner.run_agent_full",
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
        "agent.modules.agent_runtime.runner.run_agent_full",
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
        "agent.modules.agent_runtime.runner.run_agent_full",
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

    assert result == "[error] not allowed to call agent 'child-agent'."
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
        "agent.modules.agent_runtime.runner.run_agent_full",
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

    assert result == "[error] sub-agent 'child-agent' failed: boom"


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
