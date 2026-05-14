from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.modules.agents.models import AgentConfig
import agent.modules.workflows.graphs.router as router_module
from agent.modules.workflows.registry import (
    GraphRegistry,
)
from agent.modules.workflows.run_config import WorkflowContext


class _FakeChatModel:
    def __init__(self, selected_agent: str, captured: dict):
        self._selected_agent = selected_agent
        self._captured = captured

    def with_structured_output(self, schema):
        self._captured["schema"] = schema
        return self

    async def ainvoke(self, messages):
        self._captured["messages"] = messages
        return SimpleNamespace(selected_agent=self._selected_agent)


class _FakeCatalog:
    def __init__(
        self,
        *,
        agents: dict[str, AgentConfig],
        callable_map: dict[str, list[str]] | None = None,
    ):
        self._agents = dict(agents)
        self._callable_map = callable_map or {}

    def get_agent(self, name: str):
        return self._agents.get(name)

    def list_agents(self):
        return list(self._agents.values())

    def get_callable_agents(self, for_agent_name: str):
        return list(self._callable_map.get(for_agent_name, []))


def _make_agent(
    *,
    name: str,
    graph_type: str,
    description: str = "",
    tools: list[str] | None = None,
    max_context_tokens: int = 50_000,
    system_prompt: str = (
        "You are router {caller_agent_name}.\n"
        "Candidates:\n{agent_options}\n\n"
        "User request:\n{user_input}\n\n"
        "Return only selected_agent."
    ),
) -> AgentConfig:
    return AgentConfig(
        name=name,
        display_name="",
        description=description,
        graph_type=graph_type,
        model="",
        tools=list(tools or []),
        sub_agents=None,
        max_context_tokens=max_context_tokens,
        system_prompt=system_prompt,
    )


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


class _FakeGraphNoContext:
    context_schema = None

    def __init__(self, result_text: str):
        self.result_text = result_text
        self.calls: list[dict[str, object]] = []

    async def ainvoke(self, state, *, config, **kwargs):
        self.calls.append(
            {
                "state": state,
                "config": config,
                "kwargs": kwargs,
            }
        )
        return {"messages": [AIMessage(content=self.result_text)]}


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(GraphRegistry, "_graphs", {})
    monkeypatch.setattr(GraphRegistry, "_descriptions", {})
    monkeypatch.setattr(GraphRegistry, "_routeable", set())


@pytest.mark.asyncio
async def test_router_node_routes_to_selected_sub_agent(
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
        "research_chain",
        target_graph,
        description="research tasks",
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
        lambda: _FakeChatModel("researcher", captured),
    )
    monkeypatch.setattr(
        router_module,
        "get_catalog_service",
        lambda: _FakeCatalog(
            agents={
                "orchestrator": _make_agent(name="orchestrator", graph_type="router"),
                "researcher": _make_agent(
                    name="researcher",
                    graph_type="research_chain",
                    description="Research specialist",
                    tools=["websearch", "webfetch"],
                    max_context_tokens=12000,
                ),
                "default": _make_agent(name="default", graph_type="react_agent"),
            },
            callable_map={"orchestrator": ["researcher"]},
        ),
    )

    state = {"messages": [HumanMessage(content="Plan this implementation work")]}
    config = {"configurable": {"thread_id": "thread-1"}}
    runtime = SimpleNamespace(
        context=WorkflowContext(
            agent_name="orchestrator",
            working_dir="D:/repo",
            max_context_tokens=9999,
            allowed_tool_names=["call_agent"],
        )
    )

    # Call both router and execution nodes
    router_result = await router_module.llm_call_router(state, config, runtime)
    state.update(router_result)
    result = await router_module.llm_call(state, config, runtime)

    assert result["messages"][0].content == "planned-result"
    assert len(target_graph.calls) == 1
    assert target_graph.calls[0]["state"] == {"messages": state["messages"]}
    assert target_graph.calls[0]["config"] == config
    target_context = target_graph.calls[0]["context"]
    assert target_context.agent_name == "researcher"
    assert target_context.working_dir == "D:/repo"
    assert target_context.max_context_tokens == 12000
    assert target_context.allowed_tool_names == ["websearch", "webfetch"]

    system_prompt = captured["messages"][0].content
    assert "- researcher: Research specialist" in system_prompt
    assert "capabilities=" not in system_prompt
    assert "routing_hints=" not in system_prompt
    assert "workflow=" not in system_prompt
    assert "Plan this implementation work" in system_prompt


@pytest.mark.asyncio
async def test_router_node_falls_back_to_first_callable_agent_when_llm_selects_invalid_target(
    monkeypatch: pytest.MonkeyPatch,
    isolated_registry,
):
    planner_graph = _FakeGraph("planner-result")

    GraphRegistry.register("react_agent", _FakeGraph("fallback-result"), description="general")
    GraphRegistry.register("planner_chain", planner_graph, description="planning")
    GraphRegistry.register("router", _FakeGraph("router-result"), description="internal", routeable=False)

    monkeypatch.setattr(
        router_module,
        "get_chat_model",
        lambda: _FakeChatModel("outsider", {}),
    )
    monkeypatch.setattr(
        router_module,
        "get_catalog_service",
        lambda: _FakeCatalog(
            agents={
                "orchestrator": _make_agent(name="orchestrator", graph_type="router"),
                "planner": _make_agent(
                    name="planner",
                    graph_type="planner_chain",
                    tools=["list_files"],
                    max_context_tokens=4321,
                ),
                "outsider": _make_agent(name="outsider", graph_type="research_chain"),
                "default": _make_agent(name="default", graph_type="react_agent"),
            },
            callable_map={"orchestrator": ["planner"]},
        ),
    )

    state = {"messages": [HumanMessage(content="Break this task into steps")]}
    config = {"configurable": {"thread_id": "thread-1"}}
    runtime = SimpleNamespace(
        context=WorkflowContext(
            agent_name="orchestrator",
            working_dir="D:/repo",
            max_context_tokens=50000,
            allowed_tool_names=[],
        )
    )

    # Call both router and execution nodes
    router_result = await router_module.llm_call_router(state, config, runtime)
    state.update(router_result)
    result = await router_module.llm_call(state, config, runtime)

    assert result["messages"][0].content == "planner-result"
    assert planner_graph.calls[0]["context"].agent_name == "planner"


@pytest.mark.asyncio
async def test_router_node_falls_back_to_default_agent_when_no_callable_sub_agents(
    monkeypatch: pytest.MonkeyPatch,
    isolated_registry,
):
    default_graph = _FakeGraph("default-result")

    GraphRegistry.register(
        "react_agent",
        default_graph,
        description="general work",
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
        lambda: (_ for _ in ()).throw(AssertionError("LLM should not be called")),
    )
    monkeypatch.setattr(
        router_module,
        "get_catalog_service",
        lambda: _FakeCatalog(
            agents={
                "orchestrator": _make_agent(name="orchestrator", graph_type="router"),
                "default": _make_agent(
                    name="default",
                    graph_type="react_agent",
                    tools=["list_files"],
                    max_context_tokens=15000,
                ),
            },
            callable_map={"orchestrator": []},
        ),
    )

    state = {"messages": [HumanMessage(content="Plan this implementation work")]}
    config = {"configurable": {"thread_id": "thread-1"}}
    runtime = SimpleNamespace(
        context=WorkflowContext(
            agent_name="orchestrator",
            working_dir="D:/repo",
            max_context_tokens=50000,
            allowed_tool_names=[],
        )
    )

    # Call both router and execution nodes
    router_result = await router_module.llm_call_router(state, config, runtime)
    state.update(router_result)
    result = await router_module.llm_call(state, config, runtime)

    assert result["messages"][0].content == "default-result"
    assert len(default_graph.calls) == 1
    assert default_graph.calls[0]["context"].agent_name == "default"


@pytest.mark.asyncio
async def test_router_node_omits_context_for_graph_without_context_schema(
    monkeypatch: pytest.MonkeyPatch,
    isolated_registry,
):
    target_graph = _FakeGraphNoContext("planned-result")

    GraphRegistry.register("react_agent", _FakeGraph("fallback-result"), description="general")
    GraphRegistry.register("legacy_chain", target_graph, description="legacy")
    GraphRegistry.register("router", _FakeGraph("router-result"), description="internal", routeable=False)

    monkeypatch.setattr(
        router_module,
        "get_chat_model",
        lambda: _FakeChatModel("legacy-agent", {}),
    )
    monkeypatch.setattr(
        router_module,
        "get_catalog_service",
        lambda: _FakeCatalog(
            agents={
                "orchestrator": _make_agent(name="orchestrator", graph_type="router"),
                "legacy-agent": _make_agent(name="legacy-agent", graph_type="legacy_chain"),
                "default": _make_agent(name="default", graph_type="react_agent"),
            },
            callable_map={"orchestrator": ["legacy-agent"]},
        ),
    )

    state = {"messages": [HumanMessage(content="Plan this implementation work")]}
    config = {"configurable": {"thread_id": "thread-1"}}
    runtime = SimpleNamespace(
        context=WorkflowContext(
            agent_name="orchestrator",
            working_dir="D:/repo",
            max_context_tokens=50000,
            allowed_tool_names=[],
        )
    )

    # Call both router and execution nodes
    router_result = await router_module.llm_call_router(state, config, runtime)
    state.update(router_result)
    result = await router_module.llm_call(state, config, runtime)

    assert result["messages"][0].content == "planned-result"
    assert len(target_graph.calls) == 1
    assert target_graph.calls[0]["state"] == {"messages": state["messages"]}
    assert target_graph.calls[0]["config"] == config
    assert "context" not in target_graph.calls[0]


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
