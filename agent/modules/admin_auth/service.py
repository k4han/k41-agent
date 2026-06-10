from __future__ import annotations

from typing import Final

import bcrypt
from sqlalchemy import select

from agent.modules.admin_auth.models import AdminCredential
from agent.modules.admin_auth.password_policy import validate_password_or_raise
from agent.shared.infrastructure.db.session import get_async_session

DEFAULT_ADMIN_USERNAME: Final[str] = "admin"
DEFAULT_ADMIN_PASSWORD: Final[str] = "1234"
_admin_auth_service: "AdminAuthService | None" = None


def _active_admin_query():
    """Build the canonical SELECT for the active admin credential."""
    return (
        select(AdminCredential)
        .where(AdminCredential.is_active == True)
        .order_by(AdminCredential.id)
    )


class AdminAuthService:
    async def get_admin(self) -> AdminCredential | None:
        session = await get_async_session()
        async with session:
            result = await session.execute(_active_admin_query())
            return result.scalars().first()

    async def get_admin_by_id(self, admin_id: int) -> AdminCredential | None:
        session = await get_async_session()
        async with session:
            stmt = select(AdminCredential).where(
                AdminCredential.id == admin_id,
                AdminCredential.is_active == True,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    def get_password_hash(self, password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        try:
            return bcrypt.checkpw(
                plain_password.encode("utf-8"),
                hashed_password.encode("utf-8"),
            )
        except Exception:
            return False

    async def authenticate(self, password: str) -> AdminCredential | None:
        admin = await self.get_admin()
        if admin is None:
            if password != DEFAULT_ADMIN_PASSWORD:
                return None
            return await self.set_admin_password(DEFAULT_ADMIN_PASSWORD)
        if not self.verify_password(password, admin.password_hash):
            return None
        return admin

    async def verify_current_password(self, password: str) -> bool:
        admin = await self.get_admin()
        if admin is None:
            return False
        return self.verify_password(password, admin.password_hash)

    async def set_admin_password(self, password: str) -> AdminCredential:
        # Validate password against security policy
        validate_password_or_raise(password)

        session = await get_async_session()
        async with session:
            try:
                result = await session.execute(_active_admin_query())
                admin = result.scalars().first()
                if admin is None:
                    admin = AdminCredential(
                        username=DEFAULT_ADMIN_USERNAME,
                        password_hash=self.get_password_hash(password),
                        is_active=True,
                    )
                    session.add(admin)
                else:
                    admin.password_hash = self.get_password_hash(password)

                await session.commit()
                await session.refresh(admin)
                return admin
            except Exception:
                await session.rollback()
                raise


def get_admin_auth_service() -> AdminAuthService:
    global _admin_auth_service
    if _admin_auth_service is None:
        _admin_auth_service = AdminAuthService()
    return _admin_auth_service
