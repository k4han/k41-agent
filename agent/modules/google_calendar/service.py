"""Business service layer for Google Calendar operations."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from agent.modules.google_calendar.auth import GoogleOAuthManager, get_google_oauth_manager
from agent.modules.google_calendar.client import GoogleCalendarClient, get_google_calendar_client
from agent.modules.google_calendar.config import get_google_calendar_settings
from agent.modules.google_calendar.models import CalendarEventItem, GoogleCalendarStatus
from agent.modules.google_calendar.repository import GoogleCalendarStore, get_google_calendar_store
from agent.shared.config import get_config_service
from agent.shared.config.constants import DEFAULT_DISPLAY_TIMEZONE, DISPLAY_TIMEZONE_CONFIG_KEY

logger = logging.getLogger(__name__)


def _get_system_timezone() -> ZoneInfo:
    """Resolve configured IANA display timezone."""
    tz_str = get_config_service().get_str(DISPLAY_TIMEZONE_CONFIG_KEY, DEFAULT_DISPLAY_TIMEZONE)
    try:
        return ZoneInfo(tz_str)
    except Exception:
        return ZoneInfo("UTC")


class GoogleCalendarService:
    """Provides high-level calendar interactions and schedule formatting."""

    def __init__(
        self,
        client: GoogleCalendarClient | None = None,
        oauth_manager: GoogleOAuthManager | None = None,
        store: GoogleCalendarStore | None = None,
    ) -> None:
        self._client = client or get_google_calendar_client()
        self._oauth = oauth_manager or get_google_oauth_manager()
        self._store = store or get_google_calendar_store()

    async def is_connected(self, user_id: str = "default") -> bool:
        """Check whether the user has a linked Google Calendar account."""
        account = await self._store.get_account(user_id)
        return account is not None and bool(account.encrypted_refresh_token or account.access_token)

    async def get_status(self, user_id: str = "default") -> GoogleCalendarStatus:
        """Get the connection status and account details."""
        settings = get_google_calendar_settings()
        account = await self._store.get_account(user_id)
        if not account:
            return GoogleCalendarStatus(
                connected=False,
                configured=settings.is_configured,
            )

        scopes_list = [s for s in (account.scopes or "").split() if s]
        return GoogleCalendarStatus(
            connected=True,
            email=account.account_email,
            configured=settings.is_configured,
            scopes=scopes_list,
            updated_at=account.updated_at,
        )

    async def list_calendars(
        self,
        user_id: str = "default",
    ) -> list[dict[str, Any]]:
        """List calendars available to the connected account (single-account mode)."""
        return await self._client.list_calendars(user_id=user_id)

    async def get_event(
        self,
        event_id: str,
        user_id: str = "default",
        calendar_id: str = "primary",
    ) -> CalendarEventItem:
        """Fetch a specific event by ID."""
        return await self._client.get_event(
            event_id=event_id,
            user_id=user_id,
            calendar_id=calendar_id,
        )

    async def list_events(
        self,
        user_id: str = "default",
        time_min: str | None = None,
        time_max: str | None = None,
        query: str | None = None,
        max_results: int = 20,
        calendar_id: str = "primary",
    ) -> list[CalendarEventItem]:
        """List upcoming events within a timeframe."""
        return await self._client.list_events(
            user_id=user_id,
            calendar_id=calendar_id,
            time_min=time_min,
            time_max=time_max,
            query=query,
            max_results=max_results,
        )

    async def get_agenda(
        self,
        user_id: str = "default",
        date_str: str | None = None,
        tz_name: str | None = None,
        calendar_id: str = "primary",
    ) -> str:
        """Retrieve and format a human-readable agenda for a specific date (defaults to today)."""
        tz = ZoneInfo(tz_name) if tz_name else _get_system_timezone()

        if date_str:
            try:
                target_date = date.fromisoformat(date_str)
            except ValueError:
                # Handle YYYY-MM-DD or parse from string
                target_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        else:
            target_date = datetime.now(tz).date()

        start_dt = datetime.combine(target_date, time.min, tzinfo=tz)
        end_dt = datetime.combine(target_date, time.max, tzinfo=tz)

        events = await self._client.list_events(
            user_id=user_id,
            calendar_id=calendar_id,
            time_min=start_dt.isoformat(),
            time_max=end_dt.isoformat(),
            max_results=50,
        )

        formatted_date = target_date.strftime("%A, %Y-%m-%d")
        if not events:
            return f"📅 Agenda for {formatted_date}: No events scheduled."

        lines = [f"📅 **Agenda for {formatted_date}** ({len(events)} event{'s' if len(events) > 1 else ''}):"]
        for ev in events:
            # Format time display
            start_display = ev.start
            end_display = ev.end
            try:
                if "T" in ev.start:
                    s_dt = datetime.fromisoformat(ev.start).astimezone(tz)
                    e_dt = datetime.fromisoformat(ev.end).astimezone(tz)
                    time_range = f"{s_dt.strftime('%H:%M')} - {e_dt.strftime('%H:%M')}"
                else:
                    time_range = "All day"
            except Exception:
                time_range = f"{start_display} - {end_display}"

            meet_info = f" | [Google Meet]({ev.meet_link})" if ev.meet_link else ""
            loc_info = f" ({ev.location})" if ev.location else ""
            lines.append(f"- **{time_range}**: {ev.summary}{loc_info}{meet_info} `[id: {ev.id}]`")

        return "\n".join(lines)

    async def check_availability(
        self,
        start_time: str,
        end_time: str,
        user_id: str = "default",
        calendar_id: str = "primary",
    ) -> dict[str, Any]:
        """Check if user has any conflicts during a specified time interval."""
        busy_slots = await self._client.get_free_busy(
            time_min=start_time,
            time_max=end_time,
            user_id=user_id,
            calendars=[calendar_id],
        )
        is_free = len(busy_slots) == 0
        return {
            "available": is_free,
            "conflicts": busy_slots,
            "message": "Time slot is free." if is_free else f"Found {len(busy_slots)} conflicting event(s).",
        }

    async def create_event(
        self,
        summary: str,
        start_time: str,
        end_time: str,
        user_id: str = "default",
        description: str = "",
        location: str = "",
        attendees: list[str] | None = None,
        create_meet: bool = False,
        tz_name: str | None = None,
        calendar_id: str = "primary",
    ) -> CalendarEventItem:
        """Create a new event."""
        tz_str = tz_name or str(_get_system_timezone())
        return await self._client.create_event(
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            user_id=user_id,
            calendar_id=calendar_id,
            description=description,
            location=location,
            attendees=attendees,
            create_meet=create_meet,
            timezone=tz_str,
        )

    async def update_event(
        self,
        event_id: str,
        user_id: str = "default",
        summary: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        description: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
        tz_name: str | None = None,
        calendar_id: str = "primary",
    ) -> CalendarEventItem:
        """Update existing event details."""
        tz_str = tz_name or str(_get_system_timezone())
        return await self._client.update_event(
            event_id=event_id,
            user_id=user_id,
            calendar_id=calendar_id,
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            description=description,
            location=location,
            attendees=attendees,
            timezone=tz_str,
        )

    async def delete_event(
        self,
        event_id: str,
        user_id: str = "default",
        calendar_id: str = "primary",
    ) -> bool:
        """Delete an event from Google Calendar."""
        return await self._client.delete_event(
            event_id=event_id, user_id=user_id, calendar_id=calendar_id
        )


_global_calendar_service: GoogleCalendarService | None = None


def get_google_calendar_service() -> GoogleCalendarService:
    """Return the global GoogleCalendarService singleton."""
    global _global_calendar_service
    if _global_calendar_service is None:
        _global_calendar_service = GoogleCalendarService()
    return _global_calendar_service


__all__ = [
    "GoogleCalendarService",
    "get_google_calendar_service",
]
