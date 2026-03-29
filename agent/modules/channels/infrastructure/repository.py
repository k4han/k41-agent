from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.modules.channels.infrastructure.models import BotSettings
from agent.shared.infrastructure.db.session import get_async_session_maker


class ChannelSettingsRepository:
    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._session_maker = session_maker or get_async_session_maker()

    async def get(self, user_id: int, platform: str) -> BotSettings | None:
        async with self._session_maker() as session:
            result = await session.execute(
                select(BotSettings).where(
                    BotSettings.user_id == user_id,
                    BotSettings.platform == platform,
                )
            )
            return result.scalar_one_or_none()

    async def list_for_user(self, user_id: int) -> list[BotSettings]:
        async with self._session_maker() as session:
            result = await session.execute(
                select(BotSettings)
                .where(BotSettings.user_id == user_id)
                .order_by(BotSettings.id.asc())
            )
            return list(result.scalars().all())

    async def upsert(
        self,
        user_id: int,
        platform: str,
        enabled: bool,
        config_json: str | None,
    ) -> BotSettings:
        async with self._session_maker() as session:
            result = await session.execute(
                select(BotSettings).where(
                    BotSettings.user_id == user_id,
                    BotSettings.platform == platform,
                )
            )
            instance = result.scalar_one_or_none()
            if instance is None:
                instance = BotSettings(
                    user_id=user_id,
                    platform=platform,
                    enabled=enabled,
                    config_json=config_json,
                )
                session.add(instance)
            else:
                instance.enabled = enabled
                instance.config_json = config_json

            await session.commit()
            await session.refresh(instance)
            return instance

    async def delete(self, user_id: int, platform: str) -> bool:
        async with self._session_maker() as session:
            result = await session.execute(
                select(BotSettings).where(
                    BotSettings.user_id == user_id,
                    BotSettings.platform == platform,
                )
            )
            instance = result.scalar_one_or_none()
            if instance is None:
                return False

            await session.delete(instance)
            await session.commit()
            return True


__all__ = ["ChannelSettingsRepository"]
