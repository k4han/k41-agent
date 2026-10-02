"""OAuth 2.0 flow and token lifecycle manager for Google Calendar."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from agent.modules.google_calendar.config import (
    DEFAULT_GOOGLE_CALENDAR_REDIRECT_URI,
    GoogleCalendarSettings,
    get_google_calendar_settings,
)
from agent.modules.google_calendar.repository import GoogleCalendarStore, get_google_calendar_store
from agent.shared.infrastructure.db import utcnow

logger = logging.getLogger(__name__)


def _ensure_aware_utc(value: datetime) -> datetime:
    """Coerce a datetime to timezone-aware UTC.

    SQLite does not persist tzinfo, so datetimes read back from the database
    are offset-naive while utcnow() is offset-aware. Comparing them raises
    TypeError, so assume naive values are already UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"


class GoogleOAuthManager:
    """Manages OAuth 2.0 authentication, code exchange, and token refresh."""

    def __init__(
        self,
        settings: GoogleCalendarSettings | None = None,
        store: GoogleCalendarStore | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._store = store or get_google_calendar_store()
        self._http_client = http_client

    def _get_settings(self) -> GoogleCalendarSettings:
        if self._settings is not None:
            return self._settings
        return get_google_calendar_settings()

    def get_authorization_url(self, state: str, redirect_uri: str | None = None) -> str:
        """Build Google OAuth2 consent URL with offline access."""
        settings = self._get_settings()
        if not settings.client_id:
            raise ValueError("Google Calendar client_id is not configured.")

        params = {
            "client_id": settings.client_id,
            "redirect_uri": redirect_uri or settings.redirect_uri,
            "response_type": "code",
            "scope": " ".join(settings.scopes),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "include_granted_scopes": "true",
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(
        self,
        code: str,
        user_id: str = "default",
        redirect_uri: str | None = None,
    ) -> dict[str, Any]:
        """Exchange authorization code for access and refresh tokens, then store them."""
        settings = self._get_settings()
        if not settings.client_id or not settings.client_secret:
            raise ValueError("Google Calendar OAuth credentials (client_id / client_secret) are missing.")

        target_redirect_uri = redirect_uri or settings.redirect_uri

        payload = {
            "code": code,
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "redirect_uri": target_redirect_uri,
            "grant_type": "authorization_code",
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(GOOGLE_TOKEN_URL, data=payload, timeout=15.0)
            if resp.status_code != 200:
                logger.error("Google token exchange failed: %s - %s", resp.status_code, resp.text)
                raise RuntimeError(f"Google OAuth token exchange failed: {resp.text}")

            token_data = resp.json()
            access_token = token_data.get("access_token", "")
            refresh_token = token_data.get("refresh_token", "")
            expires_in = int(token_data.get("expires_in", 3600))
            scope = token_data.get("scope", "")

            # Fetch user email
            user_email = ""
            userinfo_resp = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
            if userinfo_resp.status_code == 200:
                user_email = str(userinfo_resp.json().get("email", "")).strip()

            if not user_email:
                user_email = "connected_google_account"

            token_expiry = utcnow() + timedelta(seconds=expires_in)

            # If Google didn't return a new refresh_token, try to keep existing one
            if not refresh_token:
                existing_account = await self._store.get_account(user_id)
                if existing_account and existing_account.encrypted_refresh_token:
                    refresh_token = self._store.decrypt_token(existing_account.encrypted_refresh_token)

            await self._store.save_account(
                user_id=user_id,
                account_email=user_email,
                refresh_token=refresh_token,
                access_token=access_token,
                token_expiry=token_expiry,
                scopes=scope,
                is_primary=True,
            )

            return {
                "email": user_email,
                "connected": True,
                "token_expiry": token_expiry.isoformat(),
            }

    async def get_valid_access_token(self, user_id: str = "default") -> str:
        """Get an active access token, refreshing it if expired or expiring soon."""
        account = await self._store.get_account(user_id)
        if not account:
            raise RuntimeError("No Google Calendar account is connected.")

        now = utcnow()
        # If token is still valid for > 5 minutes, use it
        token_expiry = (
            _ensure_aware_utc(account.token_expiry) if account.token_expiry is not None else None
        )
        if account.access_token and token_expiry and token_expiry > now + timedelta(minutes=5):
            return account.access_token

        # Otherwise refresh using refresh_token
        refresh_token = self._store.decrypt_token(account.encrypted_refresh_token)
        if not refresh_token:
            raise RuntimeError("Google Calendar refresh token is missing. Please reconnect your account.")

        settings = self._get_settings()
        payload = {
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(GOOGLE_TOKEN_URL, data=payload, timeout=15.0)
            if resp.status_code != 200:
                logger.error("Failed to refresh Google access token: %s - %s", resp.status_code, resp.text)
                if "invalid_grant" in resp.text:
                    raise RuntimeError("Google Calendar authorization was revoked or expired. Please reconnect.")
                raise RuntimeError(f"Failed to refresh Google token: {resp.text}")

            token_data = resp.json()
            new_access_token = token_data.get("access_token", "")
            expires_in = int(token_data.get("expires_in", 3600))
            new_expiry = now + timedelta(seconds=expires_in)

            await self._store.update_access_token(account.id, new_access_token, new_expiry)
            return new_access_token

    async def revoke_account(self, user_id: str = "default") -> bool:
        """Revoke token at Google Identity and delete local credentials."""
        account = await self._store.get_account(user_id)
        if not account:
            return True

        refresh_token = self._store.decrypt_token(account.encrypted_refresh_token)
        token_to_revoke = refresh_token or account.access_token

        if token_to_revoke:
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        GOOGLE_REVOKE_URL,
                        params={"token": token_to_revoke},
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        timeout=10.0,
                    )
            except Exception as exc:
                logger.warning("Could not revoke token with Google servers: %s", exc)

        return await self._store.delete_account(user_id)


def get_google_oauth_manager(container=None) -> GoogleOAuthManager:
    """Return container-scoped OAuth manager."""
    from agent.bootstrap.container import require_active_container

    return require_active_container(container).google_oauth_manager


__all__ = [
    "GoogleOAuthManager",
    "get_google_oauth_manager",
]
