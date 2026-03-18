from agent.persistence.database import close_engine, initialize_engine
from agent.persistence.store import close_checkpointer, get_checkpointer, initialize_checkpointer


__all__ = [
    "initialize_persistence",
    "close_persistence",
    "get_checkpointer",
]


async def initialize_persistence() -> None:
    """Initialize SQLAlchemy engine and async LangGraph checkpointer."""
    initialize_engine()
    await initialize_checkpointer()


async def close_persistence() -> None:
    """Close persistence resources for clean shutdowns and tests."""
    await close_checkpointer()
    close_engine()
