from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest
import pytest_asyncio

from agent.modules.admin_auth import get_current_admin
from agent.modules.prompt_variables import PromptVariableService
from agent.shared.infrastructure.db import Base, close_async_engine, initialize_async_engine
from agent.shared.infrastructure.db.models import load_orm_models


@pytest_asyncio.fixture()
async def prompt_variable_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    await close_async_engine()

    db_path = tmp_path / "prompt_variables.sqlite"
    db_url = f"sqlite:///{db_path.resolve().as_posix()}"

    import agent.shared.infrastructure.db.engine as engine_module
    import agent.modules.prompt_variables.service as service_module

    monkeypatch.setattr(engine_module, "get_database_url", lambda: db_url)
    engine_module._cached_database_url = None
    service_module._service = None

    load_orm_models()
    await initialize_async_engine(metadata=Base.metadata)

    try:
        yield
    finally:
        service_module._service = None
        await close_async_engine()


def _dashboard_client() -> TestClient:
    from agent.delivery.http.dashboard.router import router

    app = FastAPI()
    app.include_router(router)

    async def mock_admin(_: Request) -> str:
        return "test_admin"

    app.dependency_overrides[get_current_admin] = mock_admin
    return TestClient(app)


@pytest.mark.asyncio
async def test_prompt_variable_service_crud(prompt_variable_db) -> None:
    service = PromptVariableService()

    created = await service.create_variable(
        name="common_rules",
        value="Always answer concisely.",
    )
    assert created["placeholder"] == "{{common_rules}}"

    listed = await service.list_variables()
    user_listed = [item for item in listed if not item.get("is_system")]
    assert [item["name"] for item in user_listed] == ["common_rules"]

    updated = await service.update_variable(
        current_name="common_rules",
        name="shared_rules",
        value="Use project conventions.",
    )
    assert updated["name"] == "shared_rules"
    assert await service.value_map() == {"shared_rules": "Use project conventions."}

    await service.delete_variable("shared_rules")
    listed = await service.list_variables()
    user_listed = [item for item in listed if not item.get("is_system")]
    assert user_listed == []


@pytest.mark.asyncio
async def test_prompt_variable_service_rejects_invalid_and_duplicate_names(
    prompt_variable_db,
) -> None:
    service = PromptVariableService()

    with pytest.raises(ValueError, match="must start with a letter"):
        await service.create_variable(name="bad.name", value="x")

    await service.create_variable(name="common_rules", value="x")
    with pytest.raises(FileExistsError):
        await service.create_variable(name="common_rules", value="y")


@pytest.mark.asyncio
async def test_prompt_variable_dashboard_api_crud(prompt_variable_db) -> None:
    client = _dashboard_client()

    empty = client.get("/dashboard-api/prompt-variables")
    assert empty.status_code == 200
    variables = empty.json()["variables"]
    assert len(variables) == 4
    assert all(var["is_system"] is True for var in variables)

    created = client.post(
        "/prompt-variables",
        json={"name": "common_rules", "value": "Shared prompt text."},
    )
    assert created.status_code == 200
    assert created.json()["variable"]["placeholder"] == "{{common_rules}}"

    duplicate = client.post(
        "/prompt-variables",
        json={"name": "common_rules", "value": "Other text."},
    )
    assert duplicate.status_code == 409

    updated = client.put(
        "/prompt-variables/common_rules",
        json={"name": "common_rules_v2", "value": ""},
    )
    assert updated.status_code == 200
    assert updated.json()["variable"]["name"] == "common_rules_v2"
    assert updated.json()["variable"]["value"] == ""

    listed = client.get("/dashboard-api/prompt-variables")
    assert listed.status_code == 200
    user_listed = [item for item in listed.json()["variables"] if not item.get("is_system")]
    assert [item["name"] for item in user_listed] == ["common_rules_v2"]

    deleted = client.delete("/prompt-variables/common_rules_v2")
    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted", "name": "common_rules_v2"}

    missing = client.delete("/prompt-variables/common_rules_v2")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_system_prompt_variables_read_only(prompt_variable_db) -> None:
    service = PromptVariableService()
    
    with pytest.raises(ValueError, match="reserved system prompt variable"):
        await service.create_variable(name="current_time", value="custom")
        
    with pytest.raises(ValueError, match="reserved system prompt variable"):
        await service.update_variable(current_name="current_time", name="current_time", value="custom")
        
    with pytest.raises(ValueError, match="reserved system prompt variable"):
        await service.delete_variable("current_time")
