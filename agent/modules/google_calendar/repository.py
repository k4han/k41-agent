"""Persistence repository for Google Calendar OAuth accounts and encrypted tokens."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import delete, select

from agent.modules.google_calendar.models import GoogleCalendarAccount
from agent.shared.config.database_source import _default_key_path, _load_or_create_fernet_key
from agent.shared.infrastructure.db.base import utcnow
from agent.shared.infrastructure.db.session import get_async_session

logger = logging.getLogger(__name__)


class GoogleCalendarStore:
    """Repository managing Google Calendar account tokens with encryption at rest."""

    def __init__(self, key_path: Path | None = None) -> None:
        self._key_path = key_path or _default_key_path()
        self._fernet: Fernet | None = None

    def _get_fernet(self) -> Fernet:
        if self._fernet is None:
            self._fernet = Fernet(_load_or_create_fernet_key(self._key_path))
        return self._fernet

    def encrypt_token(self, token: str) -> str:
        """Encrypt a plain text token with Fernet."""
        if not token:
            return ""
        return self._get_fernet().encrypt(token.encode("utf-8")).decode("utf-8")

    def decrypt_token(self, encrypted_token: str) -> str:
        """Decrypt a Fernet encrypted token."""
        if not encrypted_token:
            return ""
        try:
            return self._get_fernet().decrypt(encrypted_token.encode("utf-8")).decode("utf-8")
        except Exception as exc:
            logger.error("Failed to decrypt Google OAuth token: %s", exc)
            return ""

    async def get_account(self, user_id: str = "default") -> GoogleCalendarAccount | None:
        """Retrieve the primary calendar account for a user."""
        session = await get_async_session()
        async with session:
            stmt = (
                select(GoogleCalendarAccount)
                .where(GoogleCalendarAccount.user_id == user_id)
                .order_by(GoogleCalendarAccount.is_primary.desc(), GoogleCalendarAccount.updated_at.desc())
            )
            result = await session.execute(stmt)
            return result.scalars().first()

    async def list_accounts(self) -> list[GoogleCalendarAccount]:
        """List all connected Google Calendar accounts."""
        session = await get_async_session()
        async with session:
            stmt = select(GoogleCalendarAccount).order_by(GoogleCalendarAccount.updated_at.desc())
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def save_account(
        self,
        *,
        user_id: str = "default",
        account_email: str,
        refresh_token: str | None = None,
        access_token: str | None = None,
        token_expiry: datetime | None = None,
        scopes: str = "",
        is_primary: bool = True,
    ) -> GoogleCalendarAccount:
        """Insert or update a Google Calendar account."""
        session = await get_async_session()
        async with session:
            stmt = select(GoogleCalendarAccount).where(
                GoogleCalendarAccount.user_id == user_id,
                GoogleCalendarAccount.account_email == account_email,
            )
            result = await session.execute(stmt)
            account = result.scalars().first()

            if account is None:
                # If this is the first account, it should be primary
                encrypted_refresh = self.encrypt_token(refresh_token) if refresh_token else ""
                account = GoogleCalendarAccount(
                    user_id=user_id,
                    account_email=account_email,
                    encrypted_refresh_token=encrypted_refresh,
                    access_token=access_token,
                    token_expiry=token_expiry,
                    scopes=scopes,
                    is_primary=is_primary,
                )
                session.add(account)
            else:
                account.account_email = account_email
                if refresh_token:
                    account.encrypted_refresh_token = self.encrypt_token(refresh_token)
                if access_token is not None:
                    account.access_token = access_token
                if token_expiry is not None:
                    account.token_expiry = token_expiry
                if scopes:
                    account.scopes = scopes
                account.is_primary = is_primary
                account.updated_at = utcnow()

            await session.commit()
            await session.refresh(account)
            return account

    async def update_access_token(
        self,
        account_id: int,
        access_token: str,
        token_expiry: datetime,
    ) -> None:
        """Update just the cached access token and expiration time."""
        session = await get_async_session()
        async with session:
            stmt = select(GoogleCalendarAccount).where(GoogleCalendarAccount.id == account_id)
            result = await session.execute(stmt)
            account = result.scalars().first()
            if account:
                account.access_token = access_token
                account.token_expiry = token_expiry
                account.updated_at = utcnow()
                await session.commit()

    async def delete_account(self, user_id: str = "default") -> bool:
        """Delete connected accounts for a given user."""
        session = await get_async_session()
        async with session:
            stmt = delete(GoogleCalendarAccount).where(GoogleCalendarAccount.user_id == user_id)
            res = await session.execute(stmt)
            await session.commit()
            return bool(res.rowcount > 0)


_global_calendar_store: GoogleCalendarStore | None = None


def get_google_calendar_store() -> GoogleCalendarStore:
    """Return the global GoogleCalendarStore singleton."""
    global _global_calendar_store
    if _global_calendar_store is None:
        _global_calendar_store = GoogleCalendarStore()
    return _global_calendar_store


__all__ = [
    "GoogleCalendarStore",
    "get_google_calendar_store",
]
