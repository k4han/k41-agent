from agent.persistence.database import (
    close_async_engine,
    get_async_session,
    get_database_type,
    get_database_url,
    get_postgres_conn_string,
    get_sqlite_conn_string,
    initialize_async_engine,
)
from agent.persistence.store import close_checkpointer, get_checkpointer, initialize_checkpointer
from agent.persistence.models import User, BotSettings, UserPreferences


__all__ = [
    "initialize_persistence",
    "close_persistence",
    "get_checkpointer",
    # Database utilities
    "get_database_url",
    "get_database_type",
    "get_sqlite_conn_string",
    "get_postgres_conn_string",
    # SQLAlchemy engine/session
    "initialize_async_engine",
    "close_async_engine",
    "get_async_session",
    # Models
    "User",
    "BotSettings",
    "UserPreferences",
]


async def initialize_persistence() -> None:
    """Initialize SQLAlchemy async engine and LangGraph checkpointer."""
    await initialize_async_engine()
    await initialize_checkpointer()


async def close_persistence() -> None:
    """Close persistence resources for clean shutdowns and tests."""
    await close_checkpointer()
    await close_async_engine()
