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

_checkpointer: BaseCheckpointSaver | None = None
_checkpointer_cm = None


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


async def initialize_checkpointer() -> BaseCheckpointSaver:
    """Create and cache a checkpointer based on database type."""
    global _checkpointer, _checkpointer_cm

    if _checkpointer is not None:
        return _checkpointer

    serde = _create_serializer()
    db_type = get_database_type()
    if db_type == "sqlite":
        _checkpointer_cm = _create_sqlite_saver(
            get_sqlite_conn_string(), serde=serde
        )
    elif db_type == "postgres":
        if not HAS_POSTGRES:
            raise ImportError(
                "PostgreSQL checkpointer not available. "
                "Install langgraph-checkpoint-postgres: pip install langgraph-checkpoint-postgres"
            )
        _checkpointer_cm = AsyncPostgresSaver.from_conn_string(
            get_postgres_conn_string(), serde=serde
        )
    else:
        raise ValueError(f"Unsupported database type: {db_type}")

    _checkpointer = await _checkpointer_cm.__aenter__()

    setup = getattr(_checkpointer, "setup", None)
    if callable(setup):
        result = setup()
        if inspect.isawaitable(result):
            await result

    return _checkpointer


def get_checkpointer() -> BaseCheckpointSaver:
    """Return the singleton checkpointer instance."""
    if _checkpointer is None:
        raise RuntimeError(
            "Checkpointer is not initialized. Call 'await initialize_checkpointer()' or "
            "'await initialize_persistence()' first."
        )
    return _checkpointer


async def close_checkpointer() -> None:
    """Close async checkpointer context and clear in-process references."""
    global _checkpointer, _checkpointer_cm

    if _checkpointer_cm is not None:
        await _checkpointer_cm.__aexit__(None, None, None)
        _checkpointer_cm = None

    _checkpointer = None


__all__ = ["close_checkpointer", "get_checkpointer", "initialize_checkpointer"]
