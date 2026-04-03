"""User preferences repository for database operations."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.shared.infrastructure.db.session import get_async_session_maker
from agent.shared.infrastructure.db.user_preferences import UserPreferences


class UserPreferencesRepository:
    """Repository for managing user preferences in the database.

    This allows per-user settings overrides stored in the database.
    Currently not actively used but reserved for future features.
    """

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._session_maker = session_maker or get_async_session_maker()

    async def _find_preference(
        self, session: AsyncSession, user_id: int, key: str
    ) -> UserPreferences | None:
        """Find a preference by user_id and key."""
        result = await session.execute(
            select(UserPreferences).where(
                UserPreferences.user_id == user_id,
                UserPreferences.key == key,
            )
        )
        return result.scalar_one_or_none()

    async def get(self, user_id: int, key: str) -> UserPreferences | None:
        async with self._session_maker() as session:
            return await self._find_preference(session, user_id, key)

    async def list_for_user(self, user_id: int) -> list[UserPreferences]:
        async with self._session_maker() as session:
            result = await session.execute(
                select(UserPreferences)
                .where(UserPreferences.user_id == user_id)
                .order_by(UserPreferences.id.asc())
            )
            return list(result.scalars().all())

    async def upsert(
        self,
        user_id: int,
        key: str,
        value: str | None,
    ) -> UserPreferences:
        async with self._session_maker() as session:
            instance = await self._find_preference(session, user_id, key)
            if instance is None:
                instance = UserPreferences(user_id=user_id, key=key, value=value)
                session.add(instance)
            else:
                instance.value = value

            await session.commit()
            await session.refresh(instance)
            return instance

    async def delete(self, user_id: int, key: str) -> bool:
        async with self._session_maker() as session:
            instance = await self._find_preference(session, user_id, key)
            if instance is None:
                return False

            await session.delete(instance)
            await session.commit()
            return True


__all__ = ["UserPreferencesRepository"]
