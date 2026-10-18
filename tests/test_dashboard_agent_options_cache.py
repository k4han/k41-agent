import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.tools import tool

from agent.delivery.http.dashboard.routes.helpers import agents as agent_helpers
from agent.modules.agents import AgentCard


def _card(name: str, *, tools: list[str] | None = None) -> AgentCard:
    return AgentCard(
        name=name,
        graph_type="react_agent",
        provider="default",
        tools=tools or [],
        source="user",
        path=f"/agents/{name}.md",
        editable=True,
        valid=True,
    )


@pytest.fixture(autouse=True)
def _clear_agent_option_caches():
    agent_helpers.invalidate_agent_options_caches()
    yield
    agent_helpers.invalidate_agent_options_caches()


@pytest.mark.asyncio
async def test_manual_tool_options_exclude_automatic_delegation(monkeypatch):
    monkeypatch.setattr(
        agent_helpers, "get_catalog_service",
        lambda: SimpleNamespace(list_agent_cards=lambda: [_card("parent", tools=["call_agent", "delegate_task", "custom"])]),
    )
    monkeypatch.setattr(agent_helpers, "find_descriptors", lambda **kwargs: [
        SimpleNamespace(name="delegate_task", category=SimpleNamespace(value="agent")),
        SimpleNamespace(name="call_agent", category=SimpleNamespace(value="agent")),
        SimpleNamespace(name="read", category=SimpleNamespace(value="coding")),
    ])
    monkeypatch.setattr(agent_helpers, "serialize_tool_config_schemas", lambda descriptors: {
        item.name: {} for item in descriptors
    })

    payload = await agent_helpers.agent_tools_payload()

    assert payload["tools"] == ["custom", "read"]
    assert "delegate_task" not in payload["tool_config_schemas"]
    assert "call_agent" not in payload["tool_config_schemas"]
    assert all("delegate_task" not in group["tools"] for group in payload["tool_groups"])
    assert all("call_agent" not in group["tools"] for group in payload["tool_groups"])


@pytest.mark.asyncio
async def test_agent_cards_payload_does_not_cache_build_invalidated_midflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCatalog:
        calls = 0

        def list_agent_cards(self) -> list[AgentCard]:
            self.calls += 1
            if self.calls == 1:
                agent_helpers.invalidate_agent_card_related_caches()
                return [_card("old")]
            return [_card("new")]

    catalog = FakeCatalog()
    monkeypatch.setattr(agent_helpers, "get_catalog_service", lambda: catalog)

    first = await agent_helpers.agent_cards_payload()
    second = await agent_helpers.agent_cards_payload()

    assert [card["name"] for card in first["cards"]] == ["old"]
    assert [card["name"] for card in second["cards"]] == ["new"]
    assert catalog.calls == 2


@pytest.mark.asyncio
async def test_tools_and_mcp_payloads_reuse_agent_cards_cache(
    isolated_container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCatalog:
        calls = 0

        def list_agent_cards(self) -> list[AgentCard]:
            self.calls += 1
            return [_card("default", tools=["read", "mcp__github__search"])]

    catalog = FakeCatalog()
    monkeypatch.setattr(agent_helpers, "get_catalog_service", lambda: catalog)
    monkeypatch.setattr(agent_helpers, "find_descriptors", lambda **kwargs: (
        [SimpleNamespace(name="mcp__github__search")]
        if kwargs.get("source") is agent_helpers.ToolSource.MCP else []
    ))
    monkeypatch.setattr(agent_helpers, "serialize_tool_config_schemas", lambda _: {})

    async def fake_ensure_mcp_loaded(*, container):
        assert container is isolated_container
        return True

    monkeypatch.setattr("agent.modules.tools.ensure_mcp_loaded", fake_ensure_mcp_loaded)

    import agent.modules.mcp as mcp_module

    monkeypatch.setattr(
        mcp_module,
        "list_mcp_installs",
        lambda: [{"server_name": "github"}],
    )
    monkeypatch.setattr(
        mcp_module,
        "list_all_agent_mcp_installs",
        lambda: {"default": [{"server_name": "github"}]},
    )

    tools = await agent_helpers.agent_tools_payload()
    mcp = await agent_helpers.agent_mcp_payload()

    assert tools["tools"] == ["read"]
    assert mcp["mcp_server_options"] == ["github"]
    assert mcp["mcp_tools"] == {"github": ["mcp__github__search"]}
    assert catalog.calls == 1


@pytest.mark.asyncio
async def test_warming_dashboard_options_does_not_connect_to_mcp(monkeypatch):
    monkeypatch.setattr(
        agent_helpers, "get_catalog_service",
        lambda: SimpleNamespace(list_agent_cards=lambda: [_card("default")]),
    )
    monkeypatch.setattr(agent_helpers, "find_descriptors", lambda **kwargs: [])
    monkeypatch.setattr("agent.modules.mcp.list_mcp_installs", lambda: [{"server_name": "github"}])
    monkeypatch.setattr("agent.modules.mcp.list_all_agent_mcp_installs", lambda: {})

    async def unavailable_mcp():
        raise AssertionError("Startup must not connect to MCP servers.")

    async def provider_options():
        return {}

    monkeypatch.setattr("agent.modules.tools.ensure_mcp_loaded", unavailable_mcp)
    monkeypatch.setattr(agent_helpers, "provider_model_options", provider_options)

    await asyncio.wait_for(agent_helpers.warm_agent_options_caches(), timeout=1)

    assert agent_helpers._agent_cards_cache is not None
    assert agent_helpers._agent_tools_cache is not None
    assert agent_helpers._agent_mcp_cache is None


@pytest.mark.asyncio
async def test_mcp_payload_timeout_preserves_catalog_and_continues_loading(isolated_container, monkeypatch):
    from agent.modules.tools.sources.mcp import McpToolSource

    @tool("mcp__github__search")
    def search() -> str:
        """Search the configured server."""
        return "result"

    async def available_tools():
        return [search]

    previous = await McpToolSource(SimpleNamespace(get_all_tools=available_tools)).aload()
    isolated_container.tool_registry_service.load_descriptors(previous)
    cancelled = asyncio.Event()
    release_loading = asyncio.Event()
    calls = 0

    async def load_tools():
        nonlocal calls
        calls += 1
        try:
            await release_loading.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return []

    isolated_container._mcp_service = SimpleNamespace(get_all_tools=load_tools)
    monkeypatch.setattr(
        agent_helpers, "get_catalog_service",
        lambda: SimpleNamespace(list_agent_cards=lambda: [_card("default")]),
    )
    monkeypatch.setattr("agent.modules.mcp.list_mcp_installs", lambda: [{"server_name": "github"}])
    monkeypatch.setattr("agent.modules.mcp.list_all_agent_mcp_installs", lambda: {})
    monkeypatch.setattr(agent_helpers, "_MCP_TOOLS_LOAD_TIMEOUT_SECONDS", 0.01)

    try:
        first = await asyncio.wait_for(agent_helpers.agent_mcp_payload(), timeout=1)
        load_task = isolated_container._mcp_load_task
        second = await asyncio.wait_for(agent_helpers.agent_mcp_payload(), timeout=1)

        assert not cancelled.is_set()
        assert first["mcp_tools"] == second["mcp_tools"] == {"github": [search.name]}
        assert agent_helpers._agent_mcp_cache is None
        assert not isolated_container._mcp_loaded
        assert calls == 1
        assert isolated_container._mcp_load_task is load_task

        release_loading.set()
        await asyncio.wait_for(load_task, timeout=1)
        third = await agent_helpers.agent_mcp_payload()

        assert third["mcp_tools"] == {"github": []}
        assert calls == 1
        assert isolated_container._mcp_loaded
    finally:
        release_loading.set()
        if isolated_container._mcp_load_task is not None:
            await asyncio.wait_for(isolated_container._mcp_load_task, timeout=1)


@pytest.mark.asyncio
async def test_concurrent_mcp_payload_timeouts_share_loading_and_eventually_publish(
    isolated_container, monkeypatch,
):
    from agent.modules.mcp import MCPServerConfig, MCPService, MCPTransport

    @tool("mcp__healthy__read")
    def healthy_read() -> str:
        """Read from the healthy server."""
        return "healthy"

    @tool("mcp__slow__read")
    def slow_read() -> str:
        """Read from the slow server."""
        return "slow"

    configs = [MCPServerConfig(name=name, transport=MCPTransport.STDIO, command="probe")
               for name in ("healthy", "slow")]
    isolated_container._mcp_service = MCPService(SimpleNamespace(list_servers=lambda: configs))
    release_loading = asyncio.Event()
    calls = {config.name: 0 for config in configs}

    async def fetch(config):
        calls[config.name] += 1
        if config.name == "slow":
            await release_loading.wait()
            return [slow_read]
        return [healthy_read]

    monkeypatch.setattr("agent.modules.mcp.service.fetch_tools_for_config", fetch)
    monkeypatch.setattr(
        agent_helpers, "get_catalog_service",
        lambda: SimpleNamespace(list_agent_cards=lambda: [_card("default")]),
    )
    monkeypatch.setattr("agent.modules.mcp.list_mcp_installs", lambda: [
        {"server_name": config.name} for config in configs
    ])
    monkeypatch.setattr("agent.modules.mcp.list_all_agent_mcp_installs", lambda: {})
    monkeypatch.setattr(agent_helpers, "_MCP_TOOLS_LOAD_TIMEOUT_SECONDS", 0.01)

    try:
        payloads = await asyncio.wait_for(asyncio.gather(*(
            agent_helpers.agent_mcp_payload() for _ in range(3)
        )), timeout=1)
        assert all(payload["mcp_tools"] == {"healthy": [], "slow": []} for payload in payloads)
        assert calls == {"healthy": 1, "slow": 1}
        assert agent_helpers._agent_mcp_cache is None
        assert not isolated_container._mcp_load_task.done()

        release_loading.set()
        await asyncio.wait_for(isolated_container._mcp_load_task, timeout=1)
        payload = await agent_helpers.agent_mcp_payload()

        assert payload["mcp_tools"] == {
            "healthy": [healthy_read.name], "slow": [slow_read.name],
        }
        assert calls == {"healthy": 1, "slow": 1}
    finally:
        release_loading.set()
        if isolated_container._mcp_load_task is not None:
            await asyncio.wait_for(isolated_container._mcp_load_task, timeout=1)


@pytest.mark.asyncio
async def test_container_shutdown_cancels_pending_dashboard_mcp_loading(isolated_container, monkeypatch):
    cancelled = asyncio.Event()

    async def load_tools():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    isolated_container._mcp_service = SimpleNamespace(get_all_tools=load_tools)
    monkeypatch.setattr(
        agent_helpers, "get_catalog_service",
        lambda: SimpleNamespace(list_agent_cards=lambda: [_card("default")]),
    )
    monkeypatch.setattr("agent.modules.mcp.list_mcp_installs", lambda: [{"server_name": "github"}])
    monkeypatch.setattr("agent.modules.mcp.list_all_agent_mcp_installs", lambda: {})
    monkeypatch.setattr(agent_helpers, "_MCP_TOOLS_LOAD_TIMEOUT_SECONDS", 0.01)

    try:
        await asyncio.wait_for(agent_helpers.agent_mcp_payload(), timeout=1)
        load_task = isolated_container._mcp_load_task
    finally:
        await asyncio.wait_for(isolated_container.close_persistence(), timeout=1)

    assert cancelled.is_set()
    assert load_task.cancelled()
    assert isolated_container._mcp_load_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("has_replacement", [False, True])
async def test_server_reload_updates_agent_options_and_registry(
    isolated_container, monkeypatch, has_replacement,
):
    from agent.delivery.http.dashboard.routes.mcp import reload_dashboard_mcp_server
    from agent.modules.mcp import MCPServerConfig, MCPService, MCPTransport

    @tool("mcp__github__old")
    def old_tool() -> str:
        """Return the original tool response."""
        return "old"

    @tool("mcp__github__new")
    def new_tool() -> str:
        """Return the replacement tool response."""
        return "new"

    config = MCPServerConfig(name="github", transport=MCPTransport.STDIO, command="probe")
    isolated_container._mcp_service = MCPService(SimpleNamespace(
        list_servers=lambda: [config], get_server=lambda name: config,
    ))
    replacement = [new_tool] if has_replacement else []
    responses = iter([[old_tool], replacement])

    async def fetch(config):
        return next(responses)

    monkeypatch.setattr("agent.modules.mcp.service.fetch_tools_for_config", fetch)
    monkeypatch.setattr(
        agent_helpers, "get_catalog_service",
        lambda: SimpleNamespace(list_agent_cards=lambda: [_card("default")]),
    )
    monkeypatch.setattr("agent.modules.mcp.list_mcp_installs", lambda: [{"server_name": "github"}])
    monkeypatch.setattr("agent.modules.mcp.list_all_agent_mcp_installs", lambda: {})

    first = await agent_helpers.agent_mcp_payload()
    assert first["mcp_tools"] == {"github": [old_tool.name]}

    reloaded = await reload_dashboard_mcp_server("github")
    second = await agent_helpers.agent_mcp_payload()

    expected = [item.name for item in replacement]
    assert [item["prefixed_name"] for item in reloaded["tools"]] == expected
    assert second["mcp_tools"] == {"github": expected}
    registry = isolated_container.tool_registry_service
    assert registry.get_tool_by_name(old_tool.name) is None
    if has_replacement:
        assert registry.get_tool_by_name(new_tool.name) is new_tool
