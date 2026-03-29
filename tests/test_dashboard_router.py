from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.delivery.http.dashboard.router import _collection_payload, router as dashboard_router
from agent.modules.channels.public import ChannelManager


async def idle_runner() -> None:
    return None


def test_collection_payload_returns_services_only() -> None:
    channel_manager = ChannelManager()
    channel_manager.register("telegram", idle_runner)
    channel_manager.register("discord", idle_runner)

    payload = _collection_payload(channel_manager)

    assert payload == {
        "services": [
            {"name": "telegram", "status": "stopped", "error": None},
            {"name": "discord", "status": "stopped", "error": None},
        ]
    }
    assert "bots" not in payload


def test_services_endpoint_returns_runtime_service_snapshot() -> None:
    app = FastAPI()
    channel_manager = ChannelManager()
    channel_manager.register("telegram", idle_runner)
    channel_manager.register("discord", idle_runner)
    app.state.channel_manager = channel_manager
    app.include_router(dashboard_router)

    client = TestClient(app)
    response = client.get("/dashboard/services")

    assert response.status_code == 200
    assert response.json() == {
        "services": [
            {"name": "telegram", "status": "stopped", "error": None},
            {"name": "discord", "status": "stopped", "error": None},
        ]
    }


def test_legacy_bots_routes_are_removed() -> None:
    app = FastAPI()
    app.state.channel_manager = ChannelManager()
    app.include_router(dashboard_router)

    client = TestClient(app)
    response = client.get("/dashboard/bots")

    assert response.status_code == 404
