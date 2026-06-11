import pytest

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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCatalog:
        calls = 0

        def list_agent_cards(self) -> list[AgentCard]:
            self.calls += 1
            return [_card("default", tools=["read_file", "mcp__github__search"])]

    catalog = FakeCatalog()
    monkeypatch.setattr(agent_helpers, "get_catalog_service", lambda: catalog)
    monkeypatch.setattr(agent_helpers, "find_descriptors", lambda **_: [])
    monkeypatch.setattr(agent_helpers, "serialize_tool_config_schemas", lambda _: {})

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

    assert tools["tools"] == ["read_file"]
    assert mcp["mcp_server_options"] == ["github"]
    assert catalog.calls == 1
