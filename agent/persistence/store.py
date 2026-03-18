from __future__ import annotations

import inspect

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agent.persistence.database import get_sqlite_conn_string

_checkpointer: AsyncSqliteSaver | None = None
_checkpointer_cm = None


async def initialize_checkpointer() -> AsyncSqliteSaver:
    """Create and cache an AsyncSqliteSaver for async graph execution."""
    global _checkpointer, _checkpointer_cm
    if _checkpointer is not None:
        return _checkpointer

    _checkpointer_cm = AsyncSqliteSaver.from_conn_string(get_sqlite_conn_string())
    _checkpointer = await _checkpointer_cm.__aenter__()

    # Setup tables once when supported by installed langgraph-checkpoint-sqlite.
    setup = getattr(_checkpointer, "setup", None)
    if callable(setup):
        result = setup()
        if inspect.isawaitable(result):
            await result

    return _checkpointer


def get_checkpointer() -> AsyncSqliteSaver:
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
