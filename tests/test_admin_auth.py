import pytest
import pytest_asyncio

import agent.shared.infrastructure.db.engine as db_engine
from agent.modules.admin_auth.service import AdminAuthService, DEFAULT_ADMIN_PASSWORD
from agent.shared.infrastructure.db.base import Base
from agent.shared.infrastructure.db.engine import close_async_engine, initialize_async_engine
from agent.shared.infrastructure.db.models import load_orm_models


class _StubConfigService:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def get_str(self, key: str, default: str = "") -> str:
        if key == "database.url":
            return self._database_url
        return default


@pytest_asyncio.fixture
async def admin_auth_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    db_path = tmp_path / "admin-auth.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_path.resolve().as_posix()}"
    monkeypatch.setattr(db_engine, "DEFAULT_DATABASE_URL", db_url)
    monkeypatch.setattr(db_engine, "_cached_database_url", None)
    monkeypatch.setattr(db_engine, "_async_engine", None)
    monkeypatch.setattr(db_engine, "_async_session_maker", None)
    monkeypatch.setattr(db_engine, "_tables_created", False)
    monkeypatch.setattr(db_engine, "get_config_service", lambda: _StubConfigService(""))

    load_orm_models()
    await initialize_async_engine(metadata=Base.metadata)
    try:
        yield
    finally:
        await close_async_engine()


@pytest.mark.asyncio
async def test_default_admin_password_creates_initial_admin(admin_auth_db) -> None:
    service = AdminAuthService()

    admin = await service.authenticate(DEFAULT_ADMIN_PASSWORD)

    assert admin is not None
    assert admin.username == "admin"
    assert await service.verify_current_password(DEFAULT_ADMIN_PASSWORD) is True


@pytest.mark.asyncio
async def test_wrong_initial_password_does_not_create_admin(admin_auth_db) -> None:
    service = AdminAuthService()

    admin = await service.authenticate("wrong")

    assert admin is None
    assert await service.get_admin() is None


@pytest.mark.asyncio
async def test_default_password_does_not_override_existing_admin(admin_auth_db) -> None:
    service = AdminAuthService()
    await service.set_admin_password("custom")

    default_admin = await service.authenticate(DEFAULT_ADMIN_PASSWORD)
    custom_admin = await service.authenticate("custom")

    assert default_admin is None
    assert custom_admin is not None
