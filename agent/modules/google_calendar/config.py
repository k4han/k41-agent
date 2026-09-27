"""Configuration models and helpers for Google Calendar integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.shared.config import get_config_service
from agent.shared.infrastructure.validation import is_placeholder_value

DEFAULT_GOOGLE_CALENDAR_REDIRECT_URI = "http://localhost:4141/integrations/google/callback"
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


def get_google_calendar_settings() -> GoogleCalendarSettings:
    """Load Google Calendar settings from the application config service."""
    cfg = get_config_service()
    enabled = cfg.get_bool("google_calendar.enabled", True)
    client_id = cfg.get_str("google_calendar.client_id", "")
    client_secret = cfg.get_str("google_calendar.client_secret", "")
    redirect_uri = cfg.get_str("google_calendar.redirect_uri", DEFAULT_GOOGLE_CALENDAR_REDIRECT_URI)

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
    "GoogleCalendarSettings",
    "get_google_calendar_settings",
]
