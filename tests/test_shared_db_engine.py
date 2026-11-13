import asyncio
import sqlite3
from contextlib import closing

import pytest
import pytest_asyncio
from sqlalchemy import MetaData, text

import agent.shared.infrastructure.db.engine as db_engine
from agent.bootstrap.container import AppContainer, create_test_container, set_active_container
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


def _set_database_url(database_url: str) -> None:
    set_active_container(AppContainer(config_service=_StubConfigService(database_url)))


def test_canonical_get_database_type_defaults_to_internal_sqlite():
    _set_database_url("")
    assert get_database_type() == "sqlite"


def test_canonical_get_database_type_postgres_variants():
    _set_database_url("postgresql://user:pass@localhost:5432/app")
    assert get_database_type() == "postgres"

    _set_database_url("postgresql+asyncpg://user:pass@localhost:5432/app")
    assert get_database_type() == "postgres"


def test_canonical_get_postgres_conn_string_preserves_query_params():
    _set_database_url(
        "postgresql+asyncpg://user:pass@db.example.com:5432/appdb?sslmode=require&application_name=k41"
    )

    conn = get_postgres_conn_string()

    assert conn.startswith("postgresql://user:pass@db.example.com:5432/appdb")
    assert "sslmode=require" in conn
    assert "application_name=k41" in conn


def test_canonical_get_sqlite_conn_string_uses_internal_sqlite():
    _set_database_url("")
    conn = get_sqlite_conn_string()
    assert conn.endswith("agent_state.db")


@pytest_asyncio.fixture
async def shared_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    db_path = tmp_path / "shared-db.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_path.resolve().as_posix()}"
    _set_database_url("")
    monkeypatch.setattr(db_engine, "DEFAULT_DATABASE_URL", db_url)

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


@pytest.mark.parametrize("driver", ["sqlite", "sqlite+aiosqlite"])
def test_sync_engine_configures_existing_database_and_new_connections(tmp_path, driver):
    path = tmp_path / "legacy.sqlite"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE sample (value INTEGER)")
        connection.execute("INSERT INTO sample VALUES (42)")
        connection.commit()
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"

    engine = db_engine.create_sync_engine(f"{driver}:///{path.as_posix()}")
    try:
        with engine.connect() as first, engine.connect() as second:
            for connection in (first, second):
                assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
                assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 30_000
                assert connection.exec_driver_sql("SELECT value FROM sample").scalar_one() == 42
        engine.dispose()
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 30_000
    finally:
        engine.dispose()


def test_sync_engine_supports_in_memory_sqlite():
    engine = db_engine.create_sync_engine("sqlite:///:memory:")
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "memory"
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 30_000
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_async_engine_configures_all_pooled_connections(shared_db):
    engine = get_async_engine()
    async with engine.connect() as first, engine.connect() as second:
        for connection in (first, second):
            assert (await connection.exec_driver_sql("PRAGMA journal_mode")).scalar_one() == "wal"
            assert (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar_one() == 30_000
    await engine.dispose()
    async with engine.connect() as connection:
        assert (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar_one() == 30_000


@pytest.mark.asyncio
async def test_async_engine_supports_in_memory_sqlite(tmp_path):
    container = create_test_container(tmp_path=tmp_path, database_url="sqlite+aiosqlite:///:memory:")
    engine = await initialize_async_engine(metadata=MetaData(), container=container)
    try:
        async with engine.connect() as connection:
            assert (await connection.exec_driver_sql("PRAGMA journal_mode")).scalar_one() == "memory"
            assert (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar_one() == 30_000
    finally:
        await close_async_engine(container=container)


@pytest.mark.asyncio
async def test_sqlite_writer_can_commit_while_reader_holds_snapshot(shared_db):
    engine = get_async_engine()
    async with engine.begin() as connection:
        await connection.exec_driver_sql("CREATE TABLE sample (value INTEGER)")
        await connection.exec_driver_sql("INSERT INTO sample VALUES (0)")

    async with engine.connect() as reader, engine.connect() as writer:
        await reader.exec_driver_sql("BEGIN")
        assert (await reader.exec_driver_sql("SELECT value FROM sample")).scalar_one() == 0

        await writer.exec_driver_sql("UPDATE sample SET value = 1")
        await asyncio.wait_for(writer.commit(), timeout=2)

        assert (await reader.exec_driver_sql("SELECT value FROM sample")).scalar_one() == 0
        await reader.rollback()
        assert (await reader.exec_driver_sql("SELECT value FROM sample")).scalar_one() == 1


@pytest.mark.asyncio
async def test_checkpointer_waits_for_writer_beyond_previous_default_timeout(shared_db):
    from agent.modules.workflows.checkpoint.store import _create_serializer, _create_sqlite_saver

    engine = get_async_engine()
    async with engine.begin() as connection:
        await connection.exec_driver_sql("CREATE TABLE sample (value INTEGER)")

    async with _create_sqlite_saver(get_sqlite_conn_string(), _create_serializer()) as saver:
        await saver.setup()
        async with saver.conn.execute("PRAGMA busy_timeout") as cursor:
            assert (await cursor.fetchone())[0] == 30_000

        async def write_from_checkpointer():
            async with saver.conn.execute("INSERT INTO sample VALUES (2)"):
                pass
            await saver.conn.commit()

        async with engine.connect() as writer:
            await writer.exec_driver_sql("INSERT INTO sample VALUES (1)")
            pending_write = asyncio.create_task(write_from_checkpointer())
            try:
                # A competing writer must survive the driver's former five-second limit.
                done, _ = await asyncio.wait({pending_write}, timeout=5.2)
                assert not done
            finally:
                await writer.commit()
                await asyncio.wait_for(pending_write, timeout=5)

        async with engine.connect() as connection:
            rows = (await connection.exec_driver_sql("SELECT value FROM sample ORDER BY value")).scalars().all()
            assert rows == [1, 2]
