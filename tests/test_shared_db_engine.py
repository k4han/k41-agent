import pytest
import pytest_asyncio
from sqlalchemy import text

from agent.persistence.models import get_persistence_metadata
from agent.shared.infrastructure.db.engine import (
    close_async_engine,
    get_async_engine,
    get_database_type,
    get_postgres_conn_string,
    get_sqlite_conn_string,
    initialize_async_engine,
)
from agent.shared.infrastructure.db.session import get_async_session


def test_canonical_get_database_type_sqlite_variants(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./data/agent_state.db")
    assert get_database_type() == "sqlite"

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./data/agent_state.db")
    assert get_database_type() == "sqlite"


def test_canonical_get_database_type_postgres_variants(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/app")
    assert get_database_type() == "postgres"

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/app")
    assert get_database_type() == "postgres"


def test_canonical_get_postgres_conn_string_preserves_query_params(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@db.example.com:5432/appdb?sslmode=require&application_name=kaka",
    )

    conn = get_postgres_conn_string()

    assert conn.startswith("postgresql://user:pass@db.example.com:5432/appdb")
    assert "sslmode=require" in conn
    assert "application_name=kaka" in conn


def test_canonical_get_sqlite_conn_string_memory(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    assert get_sqlite_conn_string() == ":memory:"


@pytest_asyncio.fixture
async def shared_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    db_path = tmp_path / "shared-db.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.resolve().as_posix()}")
    monkeypatch.setenv("PERSISTENCE_ALLOW_ANY_PATH", "true")

    await initialize_async_engine(metadata=get_persistence_metadata())
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
