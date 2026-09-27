"""Async client for Google Calendar REST API v3."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from agent.modules.google_calendar.auth import GoogleOAuthManager, get_google_oauth_manager
from agent.modules.google_calendar.models import CalendarEventItem

logger = logging.getLogger(__name__)

CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarClient:
    """HTTP Client for Google Calendar API v3."""

    def __init__(
        self,
        oauth_manager: GoogleOAuthManager | None = None,
        base_url: str = CALENDAR_API_BASE,
    ) -> None:
        self._oauth = oauth_manager or get_google_oauth_manager()
        self._base_url = base_url.rstrip("/")

    async def _get_auth_header(self, user_id: str = "default") -> dict[str, str]:
        token = await self._oauth.get_valid_access_token(user_id)
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _parse_event_item(self, item: dict[str, Any]) -> CalendarEventItem:
        start_obj = item.get("start", {})
        end_obj = item.get("end", {})
        start_val = start_obj.get("dateTime") or start_obj.get("date") or ""
        end_val = end_obj.get("dateTime") or end_obj.get("date") or ""

        # Extract conference meet link if available
        meet_link = item.get("hangoutLink", "")
        if not meet_link:
            conf_data = item.get("conferenceData", {})
            entry_points = conf_data.get("entryPoints", [])
            for ep in entry_points:
                if ep.get("entryPointType") == "video":
                    meet_link = ep.get("uri", "")
                    break

        attendees = [
            att.get("email")
            for att in item.get("attendees", [])
            if att.get("email")
        ]

        return CalendarEventItem(
            id=item.get("id", ""),
            summary=item.get("summary", "(No Title)"),
            start=start_val,
            end=end_val,
            description=item.get("description", "") or "",
            location=item.get("location", "") or "",
            html_link=item.get("htmlLink", "") or "",
            meet_link=meet_link,
            attendees=attendees,
            status=item.get("status", "confirmed"),
        )

    async def list_events(
        self,
        user_id: str = "default",
        calendar_id: str = "primary",
        time_min: str | None = None,
        time_max: str | None = None,
        query: str | None = None,
        max_results: int = 20,
    ) -> list[CalendarEventItem]:
        """List upcoming events within a specified timeframe."""
        headers = await self._get_auth_header(user_id)
        params: dict[str, Any] = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": min(max_results, 100),
        }
        if time_min:
            params["timeMin"] = time_min
        if time_max:
            params["timeMax"] = time_max
        if query:
            params["q"] = query

        url = f"{self._base_url}/calendars/{calendar_id}/events"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, params=params, timeout=15.0)
            if resp.status_code != 200:
                logger.error("Google Calendar list_events failed: %s - %s", resp.status_code, resp.text)
                raise RuntimeError(f"Failed to list Google Calendar events: {resp.text}")

            data = resp.json()
            items = data.get("items", [])
            return [self._parse_event_item(item) for item in items]

    async def get_event(
        self,
        event_id: str,
        user_id: str = "default",
        calendar_id: str = "primary",
    ) -> CalendarEventItem:
        """Fetch a specific event by ID."""
        headers = await self._get_auth_header(user_id)
        url = f"{self._base_url}/calendars/{calendar_id}/events/{event_id}"

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=15.0)
            if resp.status_code == 404:
                raise ValueError(f"Event with ID '{event_id}' not found.")
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to get Google Calendar event: {resp.text}")

            return self._parse_event_item(resp.json())

    async def list_calendars(
        self,
        user_id: str = "default",
    ) -> list[dict[str, Any]]:
        """List calendars available to the connected account."""
        headers = await self._get_auth_header(user_id)
        url = f"{self._base_url}/users/me/calendarList"

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=15.0)
            if resp.status_code != 200:
                logger.error("Google Calendar list_calendars failed: %s - %s", resp.status_code, resp.text)
                raise RuntimeError(f"Failed to list Google calendars: {resp.text}")

            data = resp.json()
            calendars: list[dict[str, Any]] = []
            for item in data.get("items", []):
                calendars.append(
                    {
                        "id": item.get("id", ""),
                        "summary": item.get("summary", ""),
                        "primary": bool(item.get("primary", False)),
                        "time_zone": item.get("timeZone", ""),
                        "access_role": item.get("accessRole", ""),
                    }
                )
            return calendars

    async def create_event(
        self,
        summary: str,
        start_time: str,
        end_time: str,
        user_id: str = "default",
        calendar_id: str = "primary",
        description: str = "",
        location: str = "",
        attendees: list[str] | None = None,
        create_meet: bool = False,
        timezone: str | None = None,
    ) -> CalendarEventItem:
        """Create a new event on Google Calendar, optionally creating a Google Meet conference."""
        headers = await self._get_auth_header(user_id)
        url = f"{self._base_url}/calendars/{calendar_id}/events"

        start_field: dict[str, str] = {}
        end_field: dict[str, str] = {}

        if "T" in start_time:
            start_field["dateTime"] = start_time
            if timezone:
                start_field["timeZone"] = timezone
        else:
            start_field["date"] = start_time

        if "T" in end_time:
            end_field["dateTime"] = end_time
            if timezone:
                end_field["timeZone"] = timezone
        else:
            end_field["date"] = end_time

        body: dict[str, Any] = {
            "summary": summary,
            "description": description,
            "location": location,
            "start": start_field,
            "end": end_field,
        }

        if attendees:
            body["attendees"] = [{"email": email.strip()} for email in attendees if email.strip()]

        params: dict[str, Any] = {}
        if create_meet:
            params["conferenceDataVersion"] = 1
            body["conferenceData"] = {
                "createRequest": {
                    "requestId": uuid.uuid4().hex,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=body, params=params, timeout=15.0)
            if resp.status_code not in (200, 201):
                logger.error("Create event failed: %s - %s", resp.status_code, resp.text)
                raise RuntimeError(f"Failed to create Google Calendar event: {resp.text}")

            return self._parse_event_item(resp.json())

    async def update_event(
        self,
        event_id: str,
        user_id: str = "default",
        calendar_id: str = "primary",
        summary: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        description: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
        timezone: str | None = None,
    ) -> CalendarEventItem:
        """Update fields of an existing event."""
        headers = await self._get_auth_header(user_id)
        url = f"{self._base_url}/calendars/{calendar_id}/events/{event_id}"

        patch_body: dict[str, Any] = {}
        if summary is not None:
            patch_body["summary"] = summary
        if description is not None:
            patch_body["description"] = description
        if location is not None:
            patch_body["location"] = location

        if start_time is not None:
            st: dict[str, str] = {}
            if "T" in start_time:
                st["dateTime"] = start_time
                if timezone:
                    st["timeZone"] = timezone
            else:
                st["date"] = start_time
            patch_body["start"] = st

        if end_time is not None:
            et: dict[str, str] = {}
            if "T" in end_time:
                et["dateTime"] = end_time
                if timezone:
                    et["timeZone"] = timezone
            else:
                et["date"] = end_time
            patch_body["end"] = et

        if attendees is not None:
            patch_body["attendees"] = [{"email": email.strip()} for email in attendees if email.strip()]

        async with httpx.AsyncClient() as client:
            resp = await client.patch(url, headers=headers, json=patch_body, timeout=15.0)
            if resp.status_code != 200:
                logger.error("Update event failed: %s - %s", resp.status_code, resp.text)
                raise RuntimeError(f"Failed to update Google Calendar event: {resp.text}")

            return self._parse_event_item(resp.json())

    async def delete_event(
        self,
        event_id: str,
        user_id: str = "default",
        calendar_id: str = "primary",
    ) -> bool:
        """Delete an event by ID."""
        headers = await self._get_auth_header(user_id)
        url = f"{self._base_url}/calendars/{calendar_id}/events/{event_id}"

        async with httpx.AsyncClient() as client:
            resp = await client.delete(url, headers=headers, timeout=15.0)
            if resp.status_code in (200, 204):
                return True
            if resp.status_code == 404:
                return False
            raise RuntimeError(f"Failed to delete Google Calendar event: {resp.text}")

    async def get_free_busy(
        self,
        time_min: str,
        time_max: str,
        user_id: str = "default",
        calendars: list[str] | None = None,
    ) -> list[dict[str, str]]:
        """Query free/busy schedule information."""
        headers = await self._get_auth_header(user_id)
        url = f"{self._base_url}/freeBusy"

        calendar_items = [{"id": cid} for cid in (calendars or ["primary"])]
        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "items": calendar_items,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, json=body, timeout=15.0)
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to query freeBusy: {resp.text}")

            data = resp.json()
            busy_list: list[dict[str, str]] = []
            for cal_id, cal_info in data.get("calendars", {}).items():
                for busy_entry in cal_info.get("busy", []):
                    busy_list.append({
                        "calendar_id": cal_id,
                        "start": busy_entry.get("start", ""),
                        "end": busy_entry.get("end", ""),
                    })
            return busy_list


_global_calendar_client: GoogleCalendarClient | None = None


def get_google_calendar_client() -> GoogleCalendarClient:
    """Return the global GoogleCalendarClient singleton."""
    global _global_calendar_client
    if _global_calendar_client is None:
        _global_calendar_client = GoogleCalendarClient()
    return _global_calendar_client


__all__ = [
    "CALENDAR_API_BASE",
    "GoogleCalendarClient",
    "get_google_calendar_client",
]
