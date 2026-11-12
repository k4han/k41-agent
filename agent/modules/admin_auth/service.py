from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from typing import Final

import bcrypt
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from agent.modules.admin_auth.models import AdminCredential
from agent.modules.admin_auth.password_policy import validate_password_or_raise
from agent.shared.infrastructure.db.session import get_async_session

DEFAULT_ADMIN_USERNAME: Final[str] = "admin"
DEFAULT_ADMIN_PASSWORD: Final[str] = "1234"


class LoginThrottled(Exception):
    def __init__(self, retry_after: int):
        super().__init__("Too many login attempts. Please try again later.")
        self.retry_after = retry_after


def _active_admin_query():
    """Build the canonical SELECT for the active admin credential."""
    return (
        select(AdminCredential)
        .where(AdminCredential.is_active == True)
        .order_by(AdminCredential.id)
    )


class AdminAuthService:
    def __init__(self) -> None:
        self._login_attempts: deque[tuple[float, str]] = deque()

    def check_login_allowed(self, client: str) -> None:
        """Reserve an attempt before any password hashing, with bounded storage."""
        now = time.monotonic()
        while self._login_attempts and self._login_attempts[0][0] <= now - 60:
            self._login_attempts.popleft()
        client_attempts = [
            stamp for stamp, key in self._login_attempts if key == client
        ]
        if len(client_attempts) >= 5:
            raise LoginThrottled(max(1, math.ceil(client_attempts[0] + 60 - now)))
        if len(self._login_attempts) >= 30:
            raise LoginThrottled(
                max(1, math.ceil(self._login_attempts[0][0] + 60 - now))
            )
        self._login_attempts.append((now, client))

    async def has_secure_password(self) -> bool:
        admin = await self.get_admin()
        return admin is not None and not await asyncio.to_thread(
            self.verify_password, DEFAULT_ADMIN_PASSWORD, admin.password_hash
        )

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
        if admin is None and password == DEFAULT_ADMIN_PASSWORD:
            admin = await self._create_default_admin()
        if admin is None:
            return None
        if not await asyncio.to_thread(
            self.verify_password, password, admin.password_hash
        ):
            return None
        return admin

    async def _create_default_admin(self) -> AdminCredential:
        """Create bootstrap credentials without replacing a concurrent password reset."""
        password_hash = await asyncio.to_thread(
            self.get_password_hash, DEFAULT_ADMIN_PASSWORD
        )
        session = await get_async_session()
        async with session:
            admin = AdminCredential(
                username=DEFAULT_ADMIN_USERNAME,
                password_hash=password_hash,
                is_active=True,
            )
            session.add(admin)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await self.get_admin()
                if existing is None:
                    raise
                return existing
            await session.refresh(admin)
            return admin

    async def verify_current_password(self, password: str) -> bool:
        admin = await self.get_admin()
        if admin is None:
            return False
        return await asyncio.to_thread(
            self.verify_password, password, admin.password_hash
        )

    async def revoke_sessions(self, admin_id: int) -> None:
        session = await get_async_session()
        async with session:
            await session.execute(
                update(AdminCredential)
                .where(AdminCredential.id == admin_id)
                .values(session_version=AdminCredential.session_version + 1)
            )
            await session.commit()

    async def set_admin_password(
        self,
        password: str,
        *,
        validate_policy: bool = True,
    ) -> AdminCredential:
        if validate_policy:
            validate_password_or_raise(password)

        password_hash = await asyncio.to_thread(self.get_password_hash, password)
        session = await get_async_session()
        async with session:
            try:
                result = await session.execute(_active_admin_query())
                admin = result.scalars().first()
                if admin is None:
                    admin = AdminCredential(
                        username=DEFAULT_ADMIN_USERNAME,
                        password_hash=password_hash,
                        is_active=True,
                    )
                    session.add(admin)
                else:
                    # Increment in SQL so concurrent changes cannot reuse a version.
                    await session.execute(
                        update(AdminCredential)
                        .where(AdminCredential.id == admin.id)
                        .values(
                            password_hash=password_hash,
                            session_version=AdminCredential.session_version + 1,
                        )
                    )

                await session.commit()
                await session.refresh(admin)
                from agent.modules.browser import get_browser_service

                await get_browser_service().initialize_profiles([f"admin:{admin.id}"])
                return admin
            except Exception:
                await session.rollback()
                raise


def get_admin_auth_service(container=None) -> AdminAuthService:
    """Return container-scoped admin auth service."""
    from agent.bootstrap.container import require_active_container

    return require_active_container(container).admin_auth_service
