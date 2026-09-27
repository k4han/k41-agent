"""Google Calendar integration module."""

from __future__ import annotations

from agent.modules.google_calendar.auth import GoogleOAuthManager, get_google_oauth_manager
from agent.modules.google_calendar.client import GoogleCalendarClient, get_google_calendar_client
from agent.modules.google_calendar.config import (
    DEFAULT_GOOGLE_CALENDAR_REDIRECT_URI,
    DEFAULT_GOOGLE_CALENDAR_SCOPES,
    GoogleCalendarSettings,
    get_google_calendar_settings,
)
from agent.modules.google_calendar.migrations import migrate_google_calendar_tables
from agent.modules.google_calendar.models import (
    CalendarEventItem,
    GoogleCalendarAccount,
    GoogleCalendarStatus,
)
from agent.modules.google_calendar.repository import GoogleCalendarStore, get_google_calendar_store
from agent.modules.google_calendar.service import GoogleCalendarService, get_google_calendar_service

__all__ = [
    "GoogleOAuthManager",
    "get_google_oauth_manager",
    "GoogleCalendarClient",
    "get_google_calendar_client",
    "GoogleCalendarSettings",
    "get_google_calendar_settings",
    "GoogleCalendarAccount",
    "GoogleCalendarStatus",
    "CalendarEventItem",
    "GoogleCalendarStore",
    "get_google_calendar_store",
    "GoogleCalendarService",
    "get_google_calendar_service",
    "migrate_google_calendar_tables",
    "DEFAULT_GOOGLE_CALENDAR_REDIRECT_URI",
    "DEFAULT_GOOGLE_CALENDAR_SCOPES",
]
