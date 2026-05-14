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

    repo = FilesystemAgentRepository(tmp_path / "agents")
    repo.load()
    service = AgentCatalogService()
    service._repository = repo

    monkeypatch.setattr(
        dashboard_router_module,
        "get_catalog_service",
        lambda: service,
    )

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
        "tools": ["read_file"],
        "sub_agents": [],
        "max_context_tokens": 1000,
        "system_prompt": "You are a sample dashboard agent.",
    }


def test_agents_page_renders_cards_and_sidebar_link(dashboard_agent_client) -> None:
    client, _ = dashboard_agent_client

    response = client.get("/agents")

    assert response.status_code == 200
    assert "Agents" in response.text
    assert 'href="/agents" class="active"' in response.text
    assert 'href="/chat"' in response.text
    assert 'href="/chat?agent=default"' in response.text
    assert "default" in response.text
    assert "Clone" in response.text
    assert "refreshRemoteModelCatalog" in response.text
    assert "/providers/models?refresh=true" in response.text
    assert "Leaf (cannot call agents)" not in response.text
    assert "Allow selected agents" not in response.text

    index_response = client.get("/")
    assert index_response.status_code == 200
    assert 'href="/agents"' in index_response.text
    assert 'href="/chat"' in index_response.text


def test_chat_page_renders_agent_playground(dashboard_agent_client) -> None:
    client, _ = dashboard_agent_client

    response = client.get("/chat?agent=default")

    assert response.status_code == 200
    assert "Agent Chat" in response.text
    assert 'href="/chat" class="active"' in response.text
    assert 'id="agent-select"' in response.text
    assert 'id="prompt-input"' in response.text
    assert "tool-call" in response.text
    assert "appendToolCall" in response.text
    assert "appendToolResult" in response.text
    assert "Waiting for tool result" in response.text
    assert "fetch('/api/chat/events'" in response.text
    assert '"name": "default"' in response.text


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
    assert updated.json()["card"]["system_prompt"] == "Updated prompt."

    deleted = client.delete("/agents/cards/sample")

    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted", "name": "sample"}
    assert not (repo.user_dir / "sample.md").exists()


def test_clone_builtin_rejects_existing_user_override(dashboard_agent_client) -> None:
    client, repo = dashboard_agent_client

    first = client.post("/agents/cards/default/clone")
    second = client.post("/agents/cards/default/clone")

    assert first.status_code == 200
    assert first.json()["status"] == "cloned"
    assert (repo.user_dir / "default.md").is_file()
    assert second.status_code == 409


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
