from __future__ import annotations

import inspect
from contextlib import asynccontextmanager

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from agent.shared.infrastructure.db.engine import (
    get_database_type,
    get_postgres_conn_string,
    get_sqlite_conn_string,
)

try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False


def _create_serializer() -> JsonPlusSerializer:
    """Create a serializer with allowed custom types."""
    return JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("agent.modules.agents.models", "AgentConfig"),
        ]
    )


@asynccontextmanager
async def _create_sqlite_saver(conn_string: str, serde: JsonPlusSerializer):
    """Create AsyncSqliteSaver with custom serializer."""
    conn = await aiosqlite.connect(conn_string)
    try:
        checkpointer = AsyncSqliteSaver(conn, serde=serde)
        yield checkpointer
    finally:
        await conn.close()


async def _build_checkpointer(database_url: str | None = None) -> tuple[BaseCheckpointSaver, object]:
    from agent.bootstrap.container import require_active_container

    serde = _create_serializer()
    if database_url is not None:
        db_type = get_database_type(database_url)
    else:
        active = require_active_container()
        db_type = active.database_type
        database_url = active.database_url
    if db_type == "sqlite":
        cm = _create_sqlite_saver(get_sqlite_conn_string(database_url), serde=serde)
    elif db_type == "postgres":
        if not HAS_POSTGRES:
            raise ImportError(
                "PostgreSQL checkpointer not available. "
                "Install langgraph-checkpoint-postgres: pip install langgraph-checkpoint-postgres"
            )
        cm = AsyncPostgresSaver.from_conn_string(
            get_postgres_conn_string(database_url), serde=serde
        )
    else:
        raise ValueError(f"Unsupported database type: {db_type}")
    checkpointer = await cm.__aenter__()
    setup = getattr(checkpointer, "setup", None)
    if callable(setup):
        result = setup()
        if inspect.isawaitable(result):
            await result
    return checkpointer, cm


async def initialize_checkpointer(
    container=None, database_url: str | None = None
) -> BaseCheckpointSaver:
    """Create checkpointer in container scope."""
    from agent.bootstrap.container import require_active_container

    active = require_active_container(container)
    if active._checkpointer is not None:
        return active._checkpointer
    checkpointer, cm = await _build_checkpointer(
        active.database_url if database_url is None else database_url
    )
    active._checkpointer = checkpointer
    active._checkpointer_cm = cm
    return checkpointer


def get_checkpointer(container=None) -> BaseCheckpointSaver:
    """Return container-scoped checkpointer."""
    from agent.bootstrap.container import require_active_container

    active = require_active_container(container)
    if active._checkpointer is None:
        raise RuntimeError(
            "Checkpointer is not initialized. Call 'await initialize_checkpointer()' or "
            "'await initialize_persistence()' first."
        )
    return active._checkpointer


async def close_checkpointer(container=None) -> None:
    """Close container-scoped checkpointer."""
    from agent.bootstrap.container import require_active_container

    active = require_active_container(container)
    if active._checkpointer_cm is not None:
        try:
            await active._checkpointer_cm.__aexit__(None, None, None)
        finally:
            active._checkpointer_cm = None
            active._checkpointer = None


__all__ = [
    "close_checkpointer",
    "get_checkpointer",
    "initialize_checkpointer",
]
