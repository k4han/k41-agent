from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.modules.prompt_variables.models import PromptVariable
from agent.shared.infrastructure.db.base import utcnow
from agent.shared.infrastructure.db.session import get_async_session_maker


class PromptVariableRepository:
    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._session_maker = session_maker or get_async_session_maker()

    async def _find_by_name(
        self,
        session: AsyncSession,
        name: str,
    ) -> PromptVariable | None:
        result = await session.execute(
            select(PromptVariable).where(PromptVariable.name == name)
        )
        return result.scalar_one_or_none()

    async def list(self) -> list[PromptVariable]:
        async with self._session_maker() as session:
            result = await session.execute(
                select(PromptVariable).order_by(PromptVariable.name.asc())
            )
            return list(result.scalars().all())

    async def get(self, name: str) -> PromptVariable | None:
        async with self._session_maker() as session:
            return await self._find_by_name(session, name)

    async def create(self, *, name: str, value: str) -> PromptVariable:
        async with self._session_maker() as session:
            record = PromptVariable(name=name, value=value)
            session.add(record)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise FileExistsError(f"Prompt variable '{name}' already exists.") from exc
            await session.refresh(record)
            return record

    async def update(
        self,
        *,
        current_name: str,
        name: str,
        value: str,
    ) -> PromptVariable:
        async with self._session_maker() as session:
            record = await self._find_by_name(session, current_name)
            if record is None:
                raise FileNotFoundError(f"Prompt variable '{current_name}' does not exist.")

            if name != current_name:
                existing = await self._find_by_name(session, name)
                if existing is not None:
                    raise FileExistsError(f"Prompt variable '{name}' already exists.")

            record.name = name
            record.value = value
            record.updated_at = utcnow()
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise FileExistsError(f"Prompt variable '{name}' already exists.") from exc
            await session.refresh(record)
            return record

    async def delete(self, name: str) -> bool:
        async with self._session_maker() as session:
            record = await self._find_by_name(session, name)
            if record is None:
                return False

            await session.delete(record)
            await session.commit()
            return True


__all__ = ["PromptVariableRepository"]
