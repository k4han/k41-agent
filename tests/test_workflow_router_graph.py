from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.modules.agents.models import AgentConfig
import agent.modules.workflows.graphs.router as router_module
from agent.modules.workflows.registry import (
    GraphRegistry,
)
from agent.modules.workflows.run_config import WorkflowContext, make_context


class _FakeChatModel:
    def __init__(self, selected_agent: str, captured: dict):
        self._selected_agent = selected_agent
        self._captured = captured

    def with_structured_output(self, schema):
        self._captured["schema"] = schema
        return self

    async def ainvoke(self, messages, config=None):
        self._captured["messages"] = messages
        self._captured["config"] = config
        return SimpleNamespace(selected_agent=self._selected_agent)


def _fake_resolved_model(model: _FakeChatModel):
    return SimpleNamespace(
        model=model,
        provider_name="resolved-provider",
        provider_type="openai_compatible",
        model_name="resolved-model",
    )


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
    provider: str = "default",
    model: str = "",
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
        provider=provider,
        model=model,
        tools=list(tools or []),
        sub_agents=None,
        max_context_tokens=max_context_tokens,
        system_prompt=system_prompt,
    )


def _runtime_context(**overrides) -> WorkflowContext:
    defaults = {
        "agent_name": "orchestrator",
        "working_dir": "D:/repo",
        "max_context_tokens": 50_000,
        "allowed_tool_names": [],
    }
    defaults.update(overrides)
    return make_context(**defaults)


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
        "get_resolved_chat_model",
        lambda provider_name=None, model=None: _fake_resolved_model((
            captured.__setitem__("provider", provider_name),
            captured.__setitem__("model", model),
            _FakeChatModel("researcher", captured),
        )[2]),
    )
    monkeypatch.setattr(
        router_module,
        "get_catalog_service",
        lambda: _FakeCatalog(
            agents={
                "orchestrator": _make_agent(
                    name="orchestrator",
                    graph_type="router",
                    provider="router-provider",
                    model="router-model",
                ),
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
        context=_runtime_context(
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
    assert target_context.workspace.locator == str(Path("D:/repo").resolve())
    assert target_context.max_context_tokens == 12000
    assert target_context.allowed_tool_names == ["websearch", "webfetch"]
    assert target_context.provider is None
    assert target_context.model is None
    assert captured["provider"] == "router-provider"
    assert captured["model"] == "router-model"

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
        "get_resolved_chat_model",
        lambda provider_name=None, model=None: _fake_resolved_model(
            _FakeChatModel("outsider", {})
        ),
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
    runtime = SimpleNamespace(context=_runtime_context())

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
        "get_resolved_chat_model",
        lambda provider_name=None, model=None: (_ for _ in ()).throw(
            AssertionError("LLM should not be called")
        ),
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
    runtime = SimpleNamespace(context=_runtime_context())

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
        "get_resolved_chat_model",
        lambda provider_name=None, model=None: _fake_resolved_model(
            _FakeChatModel("legacy-agent", {})
        ),
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
    runtime = SimpleNamespace(context=_runtime_context())

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


def test_router_system_resolves_prompt_variables_without_changing_missing_variables():
    system_prompt = router_module._build_router_system(
        user_input="Plan the work",
        candidates={
            "researcher": _make_agent(
                name="researcher",
                graph_type="react_agent",
                description="Research specialist",
            )
        },
        router_prompt_template=(
            "{{router_rules}}\n"
            "{{missing_rules}}\n"
            "Candidates:\n{agent_options}\n"
            "Request: {user_input}\n"
            "Caller: {caller_agent_name}"
        ),
        caller_agent_name="orchestrator",
        prompt_variables={"router_rules": "Choose the strongest specialist."},
    )

    assert "Choose the strongest specialist." in system_prompt
    assert "{{missing_rules}}" in system_prompt
    assert "- researcher: Research specialist" in system_prompt
    assert "Request: Plan the work" in system_prompt
    assert "Caller: orchestrator" in system_prompt
