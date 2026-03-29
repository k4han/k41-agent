from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.shared.infrastructure.db.engine import _get_async_session_maker


def get_async_session_maker() -> async_sessionmaker[AsyncSession]:
    """Return the initialized async session factory."""
    return _get_async_session_maker()


async def get_async_session() -> AsyncSession:
    """Create a new async session for module-owned repositories."""
    async_session_maker = get_async_session_maker()
    return async_session_maker()


__all__ = ["get_async_session", "get_async_session_maker"]
