from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import agent.modules.workflows.infrastructure.langgraph.graphs.router as router_module
from agent.modules.workflows.infrastructure.langgraph.compiled_registry import (
    GraphRegistry,
)


class _FakeChatModel:
    def __init__(self, workflow: str, captured: dict):
        self._workflow = workflow
        self._captured = captured

    async def ainvoke(self, messages):
        self._captured["messages"] = messages
        return AIMessage(content=self._workflow)


class _FakeGraph:
    def __init__(self, result_text: str):
        self.result_text = result_text
        self.calls: list[dict[str, object]] = []

    async def ainvoke(self, state, *, config, context):
        self.calls.append(
            {
                "state": state,
                "config": config,
                "context": context,
            }
        )
        return {"messages": [AIMessage(content=self.result_text)]}


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(GraphRegistry, "_graphs", {})
    monkeypatch.setattr(GraphRegistry, "_descriptions", {})
    monkeypatch.setattr(GraphRegistry, "_routeable", set())


@pytest.mark.asyncio
async def test_router_node_routes_to_dynamically_registered_workflow(
    monkeypatch: pytest.MonkeyPatch,
    isolated_registry,
):
    captured: dict = {}
    target_graph = _FakeGraph("planned-result")

    GraphRegistry.register(
        "react_agent",
        _FakeGraph("fallback-result"),
        description="general work",
    )
    GraphRegistry.register(
        "planner_chain",
        target_graph,
        description="task decomposition and execution planning",
    )
    GraphRegistry.register(
        "router",
        _FakeGraph("router-result"),
        description="internal router",
        routeable=False,
    )

    monkeypatch.setattr(
        router_module,
        "get_chat_model",
        lambda: _FakeChatModel("planner_chain", captured),
    )

    state = {"messages": [HumanMessage(content="Plan this implementation work")]}
    config = {"configurable": {"thread_id": "thread-1"}}
    runtime = SimpleNamespace(context={"service_type": "default"})

    result = await router_module._router_node(state, config, runtime)

    assert result["messages"][0].content == "planned-result"
    assert len(target_graph.calls) == 1
    assert target_graph.calls[0]["state"] == {"messages": state["messages"]}
    assert target_graph.calls[0]["config"] == config
    assert target_graph.calls[0]["context"] == runtime.context

    system_prompt = captured["messages"][0].content
    assert "planner_chain" in system_prompt
    assert "task decomposition and execution planning" in system_prompt
    assert "- router:" not in system_prompt


def test_routeable_workflows_excludes_internal_graphs(isolated_registry):
    GraphRegistry.register("react_agent", _FakeGraph("general"), description="general")
    GraphRegistry.register(
        "router",
        _FakeGraph("router"),
        description="internal router",
        routeable=False,
    )

    assert GraphRegistry.routeable_workflows() == {
        "react_agent": "general",
    }
