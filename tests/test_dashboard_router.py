from agent.adapters.fastapi.dashboard import _collection_payload
from agent.services import ServiceManager


async def idle_runner() -> None:
    return None


def test_collection_payload_returns_services_only() -> None:
    service_manager = ServiceManager()
    service_manager.register("telegram", idle_runner)
    service_manager.register("discord", idle_runner)

    payload = _collection_payload(service_manager)

    assert payload == {
        "services": [
            {"name": "telegram", "status": "stopped", "error": None},
            {"name": "discord", "status": "stopped", "error": None},
        ]
    }
    assert "bots" not in payload
