"""Regression coverage for automatic delegation and per-run restrictions."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.runtime import Runtime

from agent.modules.agents import AgentConfig
from agent.modules.agents.service import AgentCatalogService
from agent.modules.tools import ToolCategory, ToolDescriptor, ToolPolicy, ToolResolver, ToolSource
from agent.modules.tools.builtin.delegation.delegate_task import delegate_task
from agent.modules.tools.registry_service import ToolRegistryService
from agent.modules.workflows.run_config import WorkflowContext


@tool("read")
def read_stub(path: str) -> str:
    """Read a test file."""
    return path


@pytest.fixture
def delegation_catalog(monkeypatch):
    configs = {
        name: AgentConfig(
            name=name, graph_type="react_agent", provider="default",
            tools=["read"] if name == "parent" else [],
            sub_agents=["child"] if name == "parent" else None,
            system_prompt="Help the user.", description=f"{name} specialist",
        )
        for name in ("parent", "child")
    }
    catalog = AgentCatalogService(SimpleNamespace(get_cached=lambda: configs))
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: catalog)
    monkeypatch.setattr("agent.modules.mcp.list_agent_mcp_server_names", lambda name: [])
    registry = ToolRegistryService()
    registry.load_descriptors([
        ToolDescriptor(
            id=f"builtin.test.{item.name}", name=item.name, description=item.description,
            source=ToolSource.BUILTIN, category=ToolCategory.AGENT, tool=item,
        )
        for item in (read_stub, delegate_task)
    ])
    monkeypatch.setattr("agent.modules.tools.resolver.get_registry_service", lambda: registry)

    async def no_mcp():
        return True

    monkeypatch.setattr("agent.modules.tools.resolver.ensure_mcp_loaded", no_mcp)
    return catalog, configs


@pytest.mark.parametrize("saved_tools", [[], ["read"], ["read", "delegate_task"], ["read", "call_agent"]])
@pytest.mark.parametrize("targets", [None, [], ["missing"], ["parent"], ["child"], ["parent", "missing", "child"]])
def test_delegation_depends_on_callable_targets(delegation_catalog, saved_tools, targets):
    catalog, configs = delegation_catalog
    parent = configs["parent"]
    parent.tools = list(saved_tools)
    parent.sub_agents = targets

    names = {item.name for item in ToolResolver(include_mcp=False).resolve_for_agent("parent")}

    expected = set(saved_tools) - {"delegate_task", "call_agent"}
    if targets and "child" in targets:
        expected.add("delegate_task")
    assert names == expected
    assert catalog.get_callable_agents("parent") == (["child"] if "delegate_task" in expected else [])
    assert parent.tools == saved_tools


@pytest.mark.parametrize("override,expected", [
    (None, {"read", "delegate_task"}),
    ([], set()),
    (["read"], {"read"}),
    (["delegate_task"], {"delegate_task"}),
    (["call_agent"], {"delegate_task"}),
    (["read", "delegate_task", "write"], {"read", "delegate_task"}),
])
def test_delegation_respects_run_restrictions(delegation_catalog, override, expected):
    tools = ToolResolver(include_mcp=False).resolve_for_agent("parent", override_tool_names=override)
    assert {item.name for item in tools} == expected


def test_explicit_override_cannot_enable_delegation_without_targets(delegation_catalog):
    _, configs = delegation_catalog
    configs["parent"].sub_agents = []
    assert ToolResolver(include_mcp=False).resolve_for_agent("parent", override_tool_names=["delegate_task"]) == []


def test_legacy_delegation_config_uses_the_new_name():
    config = AgentConfig(
        name="parent", graph_type="react_agent", tools=["call_agent"],
        tool_configs={"call_agent": {}}, sub_agents=["child"],
    )
    assert config.tools == ["delegate_task"]
    assert config.tool_configs == {"delegate_task": {}}


def test_delegation_updates_when_target_is_removed_or_restored(delegation_catalog):
    _, configs = delegation_catalog
    resolver = ToolResolver(include_mcp=False)
    child = configs.pop("child")
    assert {item.name for item in resolver.resolve_for_agent("parent")} == {"read"}
    configs["child"] = child
    assert {item.name for item in resolver.resolve_for_agent("parent")} == {"read", "delegate_task"}


def test_explicit_deny_still_overrides_automatic_delegation(delegation_catalog):
    from dataclasses import replace

    _, configs = delegation_catalog
    policy = replace(ToolPolicy.from_agent_config(configs["parent"]), deny_tool_names=frozenset({"delegate_task"}))
    assert {item.name for item in ToolResolver(include_mcp=False).resolve_for_policy(policy)} == {"read"}


@pytest.mark.asyncio
@pytest.mark.parametrize("override,enabled", [(None, True), ([], False), (["read"], False)])
async def test_llm_binding_and_tool_execution_share_delegation_policy(
    isolated_container, delegation_catalog, monkeypatch, tmp_path, override, enabled,
):
    import agent.modules.workflows.nodes.llm as llm_module
    import agent.modules.workflows.nodes.tool as tool_module

    captured = {}

    class Model:
        def bind_tools(self, tools, **kwargs):
            captured["bound"] = {item.name for item in tools}
            return self

        async def ainvoke(self, messages, config=None):
            captured["prompt"] = messages[0].content
            return AIMessage(content="Ready.")

    monkeypatch.setattr(llm_module, "get_resolved_chat_model", lambda **kwargs: SimpleNamespace(
        model=Model(), provider_name="default", provider_type="openai_compatible", model_name="test-model",
    ))

    async def run_child(**kwargs):
        captured["child"] = kwargs
        return "Child completed."

    monkeypatch.setattr("agent.modules.agent_runtime.run_agent_full", run_child)
    runtime = Runtime(context=WorkflowContext(
        agent_name="parent", working_dir=str(tmp_path), allowed_tool_names=override,
    ))
    config = {"configurable": {"thread_id": "parent-thread"}}
    await llm_module.llm_node({"messages": [HumanMessage(content="Delegate research.")]}, config, runtime)

    assert ("delegate_task" in captured["bound"]) is enabled
    assert ("- child: child specialist" in captured["prompt"]) is enabled

    state = {"messages": [AIMessage(content="", tool_calls=[{
        "name": "delegate_task", "args": {"task": "Research.", "sub_agent": "child"}, "id": "delegate-1",
    }])]}
    result = await tool_module.tool_node(state, config, runtime)
    message = result["messages"][0]
    if enabled:
        assert message.status == "success"
        assert "Child completed." in message.content
        assert captured["child"]["agent_name"] == "child"
    else:
        assert message.status == "error"
        assert "child" not in captured
