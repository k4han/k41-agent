"""Configuration models and helpers for Google Calendar integration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from agent.shared.config import get_config_service
from agent.shared.infrastructure.validation import is_placeholder_value

DEFAULT_GOOGLE_CALENDAR_REDIRECT_URI = "http://localhost:4141/integrations/google/callback"

# Platform-managed environment variables. The OAuth App identity belongs to the
# operator. End users only connect personal accounts via OAuth tokens stored in
# google_calendar_accounts.
GOOGLE_CALENDAR_CLIENT_ID_ENV_VAR = "GOOGLE_CALENDAR_CLIENT_ID"
GOOGLE_CALENDAR_CLIENT_SECRET_ENV_VAR = "GOOGLE_CALENDAR_CLIENT_SECRET"
GOOGLE_CALENDAR_REDIRECT_URI_ENV_VAR = "GOOGLE_CALENDAR_REDIRECT_URI"


def _platform_value(env_name: str, config_key: str, default: str = "") -> str:
    """Resolve a platform value: environment first, operator YAML as fallback."""
    env_value = os.environ.get(env_name, "").strip()
    if env_value:
        return env_value
    try:
        return get_config_service().get_str(config_key, default).strip() or default
    except Exception:
        return default


def _fallback_platform_value_set(env_name: str, config_key: str) -> bool:
    """Best-effort check mirroring _platform_value precedence (env, then YAML)."""
    env_value = os.environ.get(env_name, "").strip()
    if env_value and not is_placeholder_value(env_value):
        return True
    try:
        return not is_placeholder_value(get_config_service().get_str(config_key, "").strip())
    except Exception:
        return False


def get_google_platform_env_status() -> dict[str, bool]:
    """Report which platform values are set (without leaking values)."""
    try:
        settings = get_google_calendar_settings()
        return settings.platform_env_status
    except Exception:
        return {
            "client_id": _fallback_platform_value_set(
                GOOGLE_CALENDAR_CLIENT_ID_ENV_VAR, "google_calendar.client_id"
            ),
            "client_secret": _fallback_platform_value_set(
                GOOGLE_CALENDAR_CLIENT_SECRET_ENV_VAR, "google_calendar.client_secret"
            ),
            "redirect_uri": _fallback_platform_value_set(
                GOOGLE_CALENDAR_REDIRECT_URI_ENV_VAR, "google_calendar.redirect_uri"
            ),
        }
DEFAULT_GOOGLE_CALENDAR_SCOPES = (
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
)


@dataclass(frozen=True, slots=True)
class GoogleCalendarSettings:
    """Settings required to interact with Google OAuth & Calendar API."""

    enabled: bool
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...] = DEFAULT_GOOGLE_CALENDAR_SCOPES

    @property
    def is_configured(self) -> bool:
        """Return True if Google OAuth credentials are provided."""
        return (
            self.enabled
            and bool(self.client_id)
            and not is_placeholder_value(self.client_id)
            and bool(self.client_secret)
            and not is_placeholder_value(self.client_secret)
        )

    @property
    def platform_env_status(self) -> dict[str, bool]:
        return {
            "client_id": not is_placeholder_value(self.client_id),
            "client_secret": not is_placeholder_value(self.client_secret),
            "redirect_uri": not is_placeholder_value(self.redirect_uri),
        }


def get_google_calendar_settings() -> GoogleCalendarSettings:
    """Load Google Calendar settings: enabled from DB, OAuth App from env/YAML."""
    cfg = get_config_service()
    enabled = cfg.get_bool("google_calendar.enabled", True)
    client_id = _platform_value(
        GOOGLE_CALENDAR_CLIENT_ID_ENV_VAR, "google_calendar.client_id"
    )
    client_secret = _platform_value(
        GOOGLE_CALENDAR_CLIENT_SECRET_ENV_VAR, "google_calendar.client_secret"
    )
    redirect_uri = _platform_value(
        GOOGLE_CALENDAR_REDIRECT_URI_ENV_VAR,
        "google_calendar.redirect_uri",
        DEFAULT_GOOGLE_CALENDAR_REDIRECT_URI,
    )

    return GoogleCalendarSettings(
        enabled=enabled,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scopes=DEFAULT_GOOGLE_CALENDAR_SCOPES,
    )


__all__ = [
    "DEFAULT_GOOGLE_CALENDAR_REDIRECT_URI",
    "DEFAULT_GOOGLE_CALENDAR_SCOPES",
    "GOOGLE_CALENDAR_CLIENT_ID_ENV_VAR",
    "GOOGLE_CALENDAR_CLIENT_SECRET_ENV_VAR",
    "GOOGLE_CALENDAR_REDIRECT_URI_ENV_VAR",
    "GoogleCalendarSettings",
    "get_google_calendar_settings",
    "get_google_platform_env_status",
]
