import pytest
import pytest_asyncio
from sqlalchemy import text

import agent.shared.infrastructure.db.engine as db_engine
from agent.shared.infrastructure.db.base import Base
from agent.shared.infrastructure.db.models import load_orm_models
from agent.shared.infrastructure.db.engine import (
    close_async_engine,
    get_async_engine,
    get_database_type,
    get_postgres_conn_string,
    get_sqlite_conn_string,
    initialize_async_engine,
)
from agent.shared.infrastructure.db.session import get_async_session


class _StubConfigService:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def get_str(self, key: str, default: str = "") -> str:
        if key == "database.url":
            return self._database_url
        return default


def _set_database_url(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    monkeypatch.setattr(db_engine, "_cached_database_url", None)
    monkeypatch.setattr(db_engine, "get_config_service", lambda: _StubConfigService(database_url))


def test_canonical_get_database_type_defaults_to_internal_sqlite(monkeypatch: pytest.MonkeyPatch):
    _set_database_url(monkeypatch, "")
    assert get_database_type() == "sqlite"


def test_canonical_get_database_type_postgres_variants(monkeypatch: pytest.MonkeyPatch):
    _set_database_url(monkeypatch, "postgresql://user:pass@localhost:5432/app")
    assert get_database_type() == "postgres"

    _set_database_url(
        monkeypatch,
        "postgresql+asyncpg://user:pass@localhost:5432/app",
    )
    assert get_database_type() == "postgres"


def test_canonical_get_postgres_conn_string_preserves_query_params(
    monkeypatch: pytest.MonkeyPatch,
):
    _set_database_url(
        monkeypatch,
        "postgresql+asyncpg://user:pass@db.example.com:5432/appdb?sslmode=require&application_name=kaka",
    )

    conn = get_postgres_conn_string()

    assert conn.startswith("postgresql://user:pass@db.example.com:5432/appdb")
    assert "sslmode=require" in conn
    assert "application_name=kaka" in conn


def test_canonical_get_sqlite_conn_string_uses_internal_sqlite(monkeypatch: pytest.MonkeyPatch):
    _set_database_url(monkeypatch, "")
    conn = get_sqlite_conn_string()
    assert conn.endswith("agent_state.db")


@pytest_asyncio.fixture
async def shared_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    db_path = tmp_path / "shared-db.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_path.resolve().as_posix()}"
    _set_database_url(monkeypatch, "")
    monkeypatch.setattr(db_engine, "DEFAULT_DATABASE_URL", db_url)
    monkeypatch.setattr(db_engine, "_cached_database_url", None)
    monkeypatch.setattr(db_engine, "_async_engine", None)
    monkeypatch.setattr(db_engine, "_async_session_maker", None)
    monkeypatch.setattr(db_engine, "_tables_created", False)

    load_orm_models()
    await initialize_async_engine(metadata=Base.metadata)
    try:
        yield
    finally:
        await close_async_engine()


@pytest.mark.asyncio
async def test_canonical_engine_and_session_are_usable(shared_db):
    engine = get_async_engine()
    session = await get_async_session()
    try:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1
        assert session.bind is engine
    finally:
        await session.close()
