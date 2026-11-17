from pathlib import Path
import importlib

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agent.modules.admin_auth import get_current_admin
from agent.modules.agents.repository import FilesystemAgentRepository
from agent.modules.agents.service import AgentCatalogService
from agent.modules.channels import ChannelManager


@pytest.fixture()
def dashboard_agent_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    dashboard_router_module = importlib.import_module("agent.delivery.http.dashboard.router")
    agents_routes_module = importlib.import_module(
        "agent.delivery.http.dashboard.routes.agents"
    )
    helpers_agents_module = importlib.import_module(
        "agent.delivery.http.dashboard.routes.helpers.agents"
    )

    repo = FilesystemAgentRepository(tmp_path / "agents")
    repo.load()
    service = AgentCatalogService()
    service._repository = repo

    monkeypatch.setattr(agents_routes_module, "get_catalog_service", lambda: service)
    monkeypatch.setattr(helpers_agents_module, "get_catalog_service", lambda: service)

    app = FastAPI()
    app.state.channel_manager = ChannelManager()
    app.include_router(dashboard_router_module.router)

    async def mock_admin(_: Request) -> str:
        return "test_admin"

    app.dependency_overrides[get_current_admin] = mock_admin
    return TestClient(app), repo


def _payload(name: str) -> dict:
    return {
        "name": name,
        "display_name": "Sample",
        "description": "Sample dashboard agent",
        "graph_type": "react_agent",
        "provider": "default",
        "model": "",
        "tools": ["read"],
        "sub_agents": [],
        "plan_approval_targets": [],
        "context_compact_threshold": 75,
        "system_prompt": "You are a sample dashboard agent.",
    }


def test_acp_card_api_preserves_direct_configuration(dashboard_agent_client):
    client, repo = dashboard_agent_client
    payload = {**_payload("acp-configured"), "graph_type": "acp_agent", "system_prompt": "Card prompt.",
        "tools": ["write"], "permission_mode": "auto", "tool_configs": {"write": {}},
        "acp": {"agent": "antigravity-cli", "tools": ["view_file"],
            "config_options": {"model": "acp-model", "thinking_level": "low"}}}
    response = client.post("/agents/cards", json=payload)
    assert response.status_code == 200
    assert response.json()["card"]["acp"] == payload["acp"]
    repo.reload()
    cards = client.get("/dashboard-api/agents/cards").json()["cards"]
    card = next(item for item in cards if item["name"] == payload["name"])
    assert card["system_prompt"] == "Card prompt." and card["tools"] == ["write"]
    assert card["permission_mode"] == "auto" and card["acp"] == payload["acp"]
    changed = {**payload, "system_prompt": "Updated prompt."}
    response = client.put("/agents/cards/acp-configured", json=changed)
    assert response.status_code == 200 and response.json()["card"]["system_prompt"] == "Updated prompt."
    bad = {**payload, "name": "legacy", "acp": {"base_agent": "default"}}
    assert client.post("/agents/cards", json=bad).status_code == 422


def test_acp_legacy_tools_only_card_round_trips_and_rejects_unknown_tools(dashboard_agent_client):
    client, repo = dashboard_agent_client
    payload = {**_payload("acp-tools-only"), "graph_type": "acp_agent", "permission_mode": "ask",
        "acp": {"agent": "antigravity-cli", "use_default_tools": False}}
    response = client.post("/agents/cards", json=payload)
    assert response.status_code == 200
    assert response.json()["card"]["acp"] == {"agent": "antigravity-cli", "tools": [], "config_options": {}}
    repo.reload()
    assert repo.get_card("acp-tools-only").acp.tools == []
    for settings in ({"agent": "unknown", "use_default_tools": False},
                     {"agent": "antigravity-cli", "tools": ["not_a_native_tool"]}):
        response = client.put("/agents/cards/acp-tools-only", json={**payload, "acp": settings})
        assert response.status_code == 422


@pytest.mark.parametrize("selection", [None, [], ["view_file", "generate_image"]])
def test_acp_native_catalog_and_card_selection_remain_separate_from_k41_tools(dashboard_agent_client, selection):
    client, repo = dashboard_agent_client
    catalog = client.get("/dashboard-api/agents/tools").json()
    native = {tool["name"]: tool for tool in catalog["acp_tools"]}
    assert native["send_message"]["always_enabled"] is True
    assert native["generate_image"]["always_enabled"] is False
    assert "acp:list_dir" not in catalog["tools"]
    payload = {**_payload("native-selection"), "graph_type": "acp_agent", "tools": ["list_dir"],
               "permission_mode": "ask", "system_prompt": "Visible card instructions.",
               "acp": {"agent": "antigravity-cli", "tools": selection, "use_default_tools": False,
                       "agent_name": "retired-profile", "config_options": {"model": "custom"}}}
    response = client.post("/agents/cards", json=payload)
    assert response.status_code == 200
    expected = sorted(selection) if selection is not None else None
    card = response.json()["card"]
    assert card["tools"] == ["list_dir"] and card["system_prompt"] == payload["system_prompt"]
    assert card["acp"] == {"agent": "antigravity-cli", "tools": expected, "config_options": {"model": "custom"}}
    repo.reload()
    assert repo.get_card("native-selection").acp.tools == expected


@pytest.mark.parametrize("settings", [{}, {"agent": None}, {"agent": ""}, {"agent": "antigravity-acp"}])
def test_acp_card_api_normalizes_legacy_runtime_without_losing_settings(dashboard_agent_client, settings):
    client, repo = dashboard_agent_client
    payload = {**_payload("legacy-acp"), "graph_type": "acp_agent", "permission_mode": "auto",
        "acp": {**settings, "config_options": {"model": "legacy-model"}}, "system_prompt": "Keep this prompt."}
    response = client.post("/agents/cards", json=payload)
    assert response.status_code == 200
    card = response.json()["card"]
    assert card["acp"]["agent"] == "antigravity-cli"
    assert card["acp"]["config_options"] == {"model": "legacy-model"}
    assert card["system_prompt"] == payload["system_prompt"] and card["tools"] == payload["tools"]
    repo.reload()
    assert repo.get_card("legacy-acp").acp.agent == "antigravity-cli"


def test_agents_page_serves_spa_and_agent_api_returns_cards(dashboard_agent_client) -> None:
    client, _ = dashboard_agent_client

    response = client.get("/agents", headers={"Accept": "text/html"})

    assert response.status_code == 200
    assert '<div id="root">' in response.text
    assert "/dashboard-assets/" in response.text

    cards_response = client.get("/dashboard-api/agents/cards")
    assert cards_response.status_code == 200
    cards_data = cards_response.json()
    assert any(card["name"] == "default" for card in cards_data["cards"])

    workflows_response = client.get("/dashboard-api/agents/workflows")
    assert workflows_response.status_code == 200
    assert "react_agent" in workflows_response.json()["workflows"]

    tools_response = client.get("/dashboard-api/agents/tools")
    assert tools_response.status_code == 200
    tools_data = tools_response.json()
    assert "tools" in tools_data
    assert "write_todos" in tools_data["tools"]
    assert "ask_user" in tools_data["tools"]
    assert "tool_config_schemas" in tools_data
    assert "generate_image" in tools_data["tool_config_schemas"]
    assert "tool_config_effective" in tools_data
    assert "web_search" in tools_data["tool_config_effective"]
    assert "generate_image" in tools_data["tool_config_effective"]

    index_response = client.get("/", headers={"Accept": "text/html"})
    assert index_response.status_code == 200
    assert '<div id="root">' in index_response.text


def test_chat_page_serves_spa(dashboard_agent_client) -> None:
    client, _ = dashboard_agent_client

    response = client.get("/chat?agent=default", headers={"Accept": "text/html"})

    assert response.status_code == 200
    assert '<div id="root">' in response.text
    assert "/dashboard-assets/" in response.text


def test_agent_card_crud_endpoints(dashboard_agent_client) -> None:
    client, repo = dashboard_agent_client

    created = client.post("/agents/cards", json=_payload("sample"))

    assert created.status_code == 200
    assert created.json()["status"] == "created"
    assert (repo.user_dir / "sample.md").is_file()

    listed = client.get("/agents/cards")
    assert listed.status_code == 200
    assert any(card["name"] == "sample" for card in listed.json()["cards"])

    update_payload = _payload("sample")
    update_payload["sub_agents"] = []
    update_payload["system_prompt"] = "Updated prompt."
    updated = client.put("/agents/cards/sample", json=update_payload)

    assert updated.status_code == 200
    assert updated.json()["card"]["sub_agents"] == []
    assert updated.json()["card"]["plan_approval_targets"] == []
    assert updated.json()["card"]["system_prompt"] == "Updated prompt."

    deleted = client.delete("/agents/cards/sample")

    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted", "name": "sample"}
    assert not (repo.user_dir / "sample.md").exists()


def test_agent_card_api_preserves_structured_model_effort(dashboard_agent_client) -> None:
    from agent.modules.agents.parser import parse_agent_file

    client, repo = dashboard_agent_client
    payload = {**_payload("effort-agent"), "model": {"id": "claude-opus-5-5", "effort": "low"}}
    response = client.post("/agents/cards", json=payload)
    assert response.status_code == 200
    card = response.json()["card"]
    assert card["model"] == "claude-opus-5-5"
    assert card["reasoning_effort"] == "low"
    payload = {**payload, "model": card["model"], "reasoning_effort": card["reasoning_effort"], "description": "Edited"}
    response = client.put("/agents/cards/effort-agent", json=payload)
    assert response.status_code == 200
    assert parse_agent_file(repo.user_dir / "effort-agent.md").reasoning_effort == "low"
    payload["reasoning_effort"] = "High"
    assert client.put("/agents/cards/effort-agent", json=payload).status_code == 422
    payload["model"] = {"id": "claude-opus-5-5", "effort": "low"}
    payload["reasoning_effort"] = "high"
    conflict = client.put("/agents/cards/effort-agent", json=payload)
    assert conflict.status_code == 422
    assert "Conflicting" in conflict.text
    assert parse_agent_file(repo.user_dir / "effort-agent.md").reasoning_effort == "low"


def test_agent_card_crud_preserves_tool_configs(dashboard_agent_client) -> None:
    client, repo = dashboard_agent_client
    payload = _payload("image-agent")
    payload["tools"] = ["generate_image"]
    payload["tool_configs"] = {
        "generate_image": {
            "model": "gpt-image-1",
            "size": "1024x1024",
        },
        "disabled_tool": {
            "model": "ignored",
        },
    }

    created = client.post("/agents/cards", json=payload)

    assert created.status_code == 200
    card = created.json()["card"]
    assert card["tool_configs"] == {
        "generate_image": {
            "model": "gpt-image-1",
            "size": "1024x1024",
        }
    }
    assert "tool_configs:" in (repo.user_dir / "image-agent.md").read_text(
        encoding="utf-8"
    )


def test_clone_builtin_rejects_existing_user_override(dashboard_agent_client) -> None:
    client, repo = dashboard_agent_client

    first = client.post("/agents/cards/default/clone")
    second = client.post("/agents/cards/default/clone")

    assert first.status_code == 200
    assert first.json()["status"] == "cloned"
    assert (repo.user_dir / "default.md").is_file()
    assert second.status_code == 409


def test_clone_internal_builtin_preserves_internal(dashboard_agent_client) -> None:
    client, _ = dashboard_agent_client

    response = client.post("/agents/cards/conversation-title/clone")

    assert response.status_code == 200
    card = response.json()["card"]
    assert card["internal"] is True
    assert "hidden" not in card
    assert card["source"] == "user"
    assert card["overrides_builtin"] is True


@pytest.mark.parametrize("name, internal", [
    ("custom-internal", True),
    ("conversation-title", True),
    ("conversation-title", False),
])
def test_internal_agents_cannot_be_created(dashboard_agent_client, name, internal) -> None:
    client, repo = dashboard_agent_client

    response = client.post("/agents/cards", json={**_payload(name), "internal": internal})

    assert response.status_code == 400
    assert "cloning" in response.json()["detail"]
    assert not (repo.user_dir / f"{name}.md").exists()


def test_internal_clone_can_be_configured_but_not_retyped_or_deleted(dashboard_agent_client) -> None:
    from agent.modules.agents.parser import parse_agent_file

    client, repo = dashboard_agent_client
    clone = client.post("/agents/cards/conversation-title/clone")
    assert clone.status_code == 200
    payload = {**clone.json()["card"], "system_prompt": "Generate a concise conversation title."}

    updated = client.put("/agents/cards/conversation-title", json=payload)
    assert updated.status_code == 200
    assert updated.json()["card"]["internal"] is True
    assert updated.json()["card"]["system_prompt"] == payload["system_prompt"]

    retyped = client.put("/agents/cards/conversation-title", json={**payload, "internal": False})
    assert retyped.status_code == 400
    deleted = client.delete("/agents/cards/conversation-title")
    assert deleted.status_code == 400
    persisted = parse_agent_file(repo.user_dir / "conversation-title.md")
    assert persisted.internal is True
    assert persisted.system_prompt == payload["system_prompt"]


def test_chat_agent_cannot_be_converted_to_internal(dashboard_agent_client) -> None:
    client, _ = dashboard_agent_client
    payload = _payload("chat-agent")
    assert client.post("/agents/cards", json=payload).status_code == 200

    response = client.put("/agents/cards/chat-agent", json={**payload, "internal": True})

    assert response.status_code == 400
    assert "Agent type cannot be changed" in response.json()["detail"]


@pytest.mark.parametrize("fields", [
    {"hidden": True},
    {"hidden": False},
    {"hidden": True, "internal": True},
    {"hidden": True, "internal": False},
])
def test_agent_card_api_rejects_removed_hidden_field(dashboard_agent_client, fields) -> None:
    client, repo = dashboard_agent_client
    payload = _payload("unsupported-agent")

    created = client.post("/agents/cards", json={**payload, **fields})
    assert created.status_code == 422
    assert "Replace hidden with internal" in str(created.json()["detail"])
    assert not (repo.user_dir / "unsupported-agent.md").exists()

    assert client.post("/agents/cards", json=payload).status_code == 200
    updated = client.put("/agents/cards/unsupported-agent", json={**payload, **fields})
    assert updated.status_code == 422
    assert "Replace hidden with internal" in str(updated.json()["detail"])


def test_agent_card_endpoint_rejects_invalid_router_prompt(
    dashboard_agent_client,
) -> None:
    client, _ = dashboard_agent_client
    payload = _payload("router-sample")
    payload["graph_type"] = "router"
    payload["sub_agents"] = []
    payload["system_prompt"] = "Choose a target."

    response = client.post("/agents/cards", json=payload)

    assert response.status_code == 400
    assert "Router agent system_prompt" in response.json()["detail"]


def test_reload_agents_endpoint_returns_cards(dashboard_agent_client) -> None:
    client, _ = dashboard_agent_client

    response = client.post("/agents/reload")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "reloaded"
    assert any(card["name"] == "default" for card in data["cards"])
