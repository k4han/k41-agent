from __future__ import annotations

import inspect

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agent.persistence.database import (
    get_database_type,
    get_postgres_conn_string,
    get_sqlite_conn_string,
)

# Try to import PostgreSQL checkpointer
try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False

_checkpointer: BaseCheckpointSaver | None = None
_checkpointer_cm = None


async def initialize_checkpointer() -> BaseCheckpointSaver:
    """Create and cache a checkpointer based on database type."""
    global _checkpointer, _checkpointer_cm
    if _checkpointer is not None:
        return _checkpointer

    db_type = get_database_type()

    if db_type == "sqlite":
        _checkpointer_cm = AsyncSqliteSaver.from_conn_string(get_sqlite_conn_string())
    elif db_type == "postgres":
        if not HAS_POSTGRES:
            raise ImportError(
                "PostgreSQL checkpointer not available. "
                "Install langgraph-checkpoint-postgres: pip install langgraph-checkpoint-postgres"
            )
        _checkpointer_cm = AsyncPostgresSaver.from_conn_string(get_postgres_conn_string())
    else:
        raise ValueError(f"Unsupported database type: {db_type}")

    _checkpointer = await _checkpointer_cm.__aenter__()

    # Setup tables once when supported by installed langgraph-checkpoint-sqlite/postgres.
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
            "Checkpointer is not initialized. Call 'await initialize_persistence()' first."
        )
    return _checkpointer


async def close_checkpointer() -> None:
    """Close async checkpointer context and clear in-process references."""
    global _checkpointer, _checkpointer_cm

    if _checkpointer_cm is not None:
        await _checkpointer_cm.__aexit__(None, None, None)
        _checkpointer_cm = None

    _checkpointer = None
