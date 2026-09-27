"""Google Calendar tools for listing, inspecting, creating, and modifying calendar events."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field

from agent.modules.google_calendar import get_google_calendar_service
from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory
from agent.modules.tools.result import ToolError, ToolErrorCode

logger = logging.getLogger(__name__)


def _resolve_user_id(runtime: ToolRuntime[Any, Any] | None) -> str:
    """Resolve user identifier for single-account mode.

    The integration runs in single-account mode, so every call uses the
    shared ``default`` Google account regardless of thread context.
    """
    return "default"


class CalendarListEventsInput(BaseModel):
    time_min: str | None = Field(
        default=None,
        description="Lower bound for event start time in ISO 8601 format (e.g. '2026-09-28T00:00:00+07:00').",
    )
    time_max: str | None = Field(
        default=None,
        description="Upper bound for event end time in ISO 8601 format (e.g. '2026-09-28T23:59:59+07:00').",
    )
    query: str | None = Field(
        default=None,
        description="Free text search terms to filter events by summary, description, attendee, or location.",
    )
    max_results: int = Field(
        default=20,
        description="Maximum number of events to return (default 20).",
    )
    calendar_id: str = Field(
        default="primary",
        description="Calendar identifier to query (default 'primary'). Use calendar_list_calendars to discover IDs.",
    )


class CalendarGetEventInput(BaseModel):
    event_id: str = Field(
        ...,
        description="The unique Google Calendar event ID to retrieve.",
    )
    calendar_id: str = Field(
        default="primary",
        description="Calendar identifier holding the event (default 'primary').",
    )


class CalendarGetAgendaInput(BaseModel):
    date: str | None = Field(
        default=None,
        description="Specific date to get agenda for in 'YYYY-MM-DD' format. If omitted, defaults to today.",
    )
    calendar_id: str = Field(
        default="primary",
        description="Calendar identifier to query (default 'primary').",
    )


class CalendarCheckAvailabilityInput(BaseModel):
    start_time: str = Field(
        ...,
        description="Start time of proposed slot in ISO 8601 format (e.g. '2026-09-28T14:00:00+07:00').",
    )
    end_time: str = Field(
        ...,
        description="End time of proposed slot in ISO 8601 format (e.g. '2026-09-28T15:00:00+07:00').",
    )
    calendar_id: str = Field(
        default="primary",
        description="Calendar identifier to check (default 'primary').",
    )


class CalendarCreateEventInput(BaseModel):
    summary: str = Field(
        ...,
        description="Title or subject of the event/meeting.",
    )
    start_time: str = Field(
        ...,
        description="Start time in ISO 8601 format (e.g. '2026-09-28T09:00:00+07:00') or date 'YYYY-MM-DD' for all-day events.",
    )
    end_time: str = Field(
        ...,
        description="End time in ISO 8601 format (e.g. '2026-09-28T10:00:00+07:00') or date 'YYYY-MM-DD' for all-day events.",
    )
    calendar_id: str = Field(
        default="primary",
        description="Calendar identifier to create the event in (default 'primary').",
    )
    description: str = Field(
        default="",
        description="Optional detailed notes, agenda, or description for the event.",
    )
    location: str = Field(
        default="",
        description="Optional location or room name.",
    )
    attendees: list[str] | None = Field(
        default=None,
        description="Optional list of email addresses of attendees to invite.",
    )
    create_meet: bool = Field(
        default=False,
        description="If True, automatically generates a Google Meet video conference link for this event.",
    )


class CalendarUpdateEventInput(BaseModel):
    event_id: str = Field(
        ...,
        description="The unique Google Calendar event ID to update.",
    )
    calendar_id: str = Field(
        default="primary",
        description="Calendar identifier holding the event (default 'primary').",
    )
    summary: str | None = Field(
        default=None,
        description="New title for the event if changing.",
    )
    start_time: str | None = Field(
        default=None,
        description="New start time in ISO 8601 format if rescheduling.",
    )
    end_time: str | None = Field(
        default=None,
        description="New end time in ISO 8601 format if rescheduling.",
    )
    description: str | None = Field(
        default=None,
        description="New description/notes if updating.",
    )
    location: str | None = Field(
        default=None,
        description="New location if updating.",
    )
    attendees: list[str] | None = Field(
        default=None,
        description="New list of attendee emails if updating.",
    )


class CalendarDeleteEventInput(BaseModel):
    event_id: str = Field(
        ...,
        description="The unique Google Calendar event ID to delete or cancel.",
    )
    calendar_id: str = Field(
        default="primary",
        description="Calendar identifier holding the event (default 'primary').",
    )


@register_tool(
    category=ToolCategory.SCHEDULE,
    capabilities=[ToolCapability.NETWORK],
    tags=["google_calendar", "calendar", "events"],
)
@tool(args_schema=CalendarListEventsInput)
async def calendar_list_events(
    time_min: str | None = None,
    time_max: str | None = None,
    query: str | None = None,
    max_results: int = 20,
    calendar_id: str = "primary",
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg] = None,
) -> str:
    """List scheduled events from Google Calendar within an optional time range or search query."""
    user_id = _resolve_user_id(runtime)
    service = get_google_calendar_service()

    try:
        events = await service.list_events(
            user_id=user_id,
            time_min=time_min,
            time_max=time_max,
            query=query,
            max_results=max_results,
            calendar_id=calendar_id,
        )
        if not events:
            return "No events found matching criteria."

        lines = [f"Found {len(events)} event(s):"]
        for ev in events:
            meet_str = f" [Meet: {ev.meet_link}]" if ev.meet_link else ""
            loc_str = f" ({ev.location})" if ev.location else ""
            lines.append(f"- **{ev.summary}** | {ev.start} to {ev.end}{loc_str}{meet_str} (ID: `{ev.id}`)")
        return "\n".join(lines)
    except Exception as exc:
        logger.error("calendar_list_events failed: %s", exc)
        raise ToolError(ToolErrorCode.EXECUTION_ERROR, f"Failed to list calendar events: {exc}") from exc


@register_tool(
    category=ToolCategory.SCHEDULE,
    capabilities=[ToolCapability.NETWORK],
    tags=["google_calendar", "calendar", "calendars"],
)
@tool
async def calendar_list_calendars(
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg] = None,
) -> str:
    """List Google calendars available to the connected account."""
    user_id = _resolve_user_id(runtime)
    service = get_google_calendar_service()

    try:
        calendars = await service.list_calendars(user_id=user_id)
        if not calendars:
            return "No calendars found for the connected account."
        lines = [f"Found {len(calendars)} calendar(s):"]
        for cal in calendars:
            primary = " [primary]" if cal.get("primary") else ""
            tz = f" ({cal.get('time_zone')})" if cal.get("time_zone") else ""
            lines.append(f"- **{cal.get('summary', '(No Title)')}**{primary}{tz} (ID: `{cal.get('id')}`)")
        return "\n".join(lines)
    except Exception as exc:
        logger.error("calendar_list_calendars failed: %s", exc)
        raise ToolError(ToolErrorCode.EXECUTION_ERROR, f"Failed to list calendars: {exc}") from exc


@register_tool(
    category=ToolCategory.SCHEDULE,
    capabilities=[ToolCapability.NETWORK],
    tags=["google_calendar", "calendar", "events"],
)
@tool(args_schema=CalendarGetEventInput)
async def calendar_get_event(
    event_id: str,
    calendar_id: str = "primary",
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg] = None,
) -> str:
    """Get full details of a single Google Calendar event by ID."""
    user_id = _resolve_user_id(runtime)
    service = get_google_calendar_service()

    try:
        ev = await service.get_event(event_id=event_id, user_id=user_id, calendar_id=calendar_id)
        meet_str = f"\n- Meet: {ev.meet_link}" if ev.meet_link else ""
        loc_str = f"\n- Location: {ev.location}" if ev.location else ""
        attendees = ", ".join(ev.attendees) if ev.attendees else "None"
        return (
            f"**{ev.summary}** (ID: `{ev.id}`)\n"
            f"- Time: {ev.start} to {ev.end}"
            f"{loc_str}{meet_str}\n"
            f"- Attendees: {attendees}\n"
            f"- Status: {ev.status}"
            + (f"\n- Description: {ev.description}" if ev.description else "")
            + (f"\n- Link: {ev.html_link}" if ev.html_link else "")
        )
    except Exception as exc:
        logger.error("calendar_get_event failed: %s", exc)
        raise ToolError(ToolErrorCode.EXECUTION_ERROR, f"Failed to get calendar event: {exc}") from exc


@register_tool(
    category=ToolCategory.SCHEDULE,
    capabilities=[ToolCapability.NETWORK],
    tags=["google_calendar", "calendar", "agenda"],
)
@tool(args_schema=CalendarGetAgendaInput)
async def calendar_get_agenda(
    date: str | None = None,
    calendar_id: str = "primary",
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg] = None,
) -> str:
    """Get a nicely formatted agenda of all events scheduled for a single day (defaults to today)."""
    user_id = _resolve_user_id(runtime)
    service = get_google_calendar_service()

    try:
        return await service.get_agenda(user_id=user_id, date_str=date, calendar_id=calendar_id)
    except Exception as exc:
        logger.error("calendar_get_agenda failed: %s", exc)
        raise ToolError(ToolErrorCode.EXECUTION_ERROR, f"Failed to get agenda: {exc}") from exc


@register_tool(
    category=ToolCategory.SCHEDULE,
    capabilities=[ToolCapability.NETWORK],
    tags=["google_calendar", "calendar", "freebusy"],
)
@tool(args_schema=CalendarCheckAvailabilityInput)
async def calendar_check_availability(
    start_time: str,
    end_time: str,
    calendar_id: str = "primary",
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg] = None,
) -> str:
    """Check whether a specific time slot is free or has conflicting events on Google Calendar."""
    user_id = _resolve_user_id(runtime)
    service = get_google_calendar_service()

    try:
        res = await service.check_availability(
            start_time=start_time, end_time=end_time, user_id=user_id, calendar_id=calendar_id
        )
        if res.get("available"):
            return f"Time slot {start_time} - {end_time} is FREE. No conflicts."
        conflicts = res.get("conflicts", [])
        return f"Time slot {start_time} - {end_time} has {len(conflicts)} conflict(s): {conflicts}"
    except Exception as exc:
        logger.error("calendar_check_availability failed: %s", exc)
        raise ToolError(ToolErrorCode.EXECUTION_ERROR, f"Failed to check availability: {exc}") from exc


@register_tool(
    category=ToolCategory.SCHEDULE,
    capabilities=[ToolCapability.NETWORK, ToolCapability.MUTATES_STATE],
    tags=["google_calendar", "calendar", "create"],
)
@tool(args_schema=CalendarCreateEventInput)
async def calendar_create_event(
    summary: str,
    start_time: str,
    end_time: str,
    description: str = "",
    location: str = "",
    attendees: list[str] | None = None,
    create_meet: bool = False,
    calendar_id: str = "primary",
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg] = None,
) -> str:
    """Create a new event on Google Calendar, with optional Google Meet link and attendees."""
    user_id = _resolve_user_id(runtime)
    service = get_google_calendar_service()

    try:
        event = await service.create_event(
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            user_id=user_id,
            description=description,
            location=location,
            attendees=attendees,
            create_meet=create_meet,
            calendar_id=calendar_id,
        )
        meet_msg = f"\n- Google Meet: {event.meet_link}" if event.meet_link else ""
        link_msg = f"\n- Event Link: {event.html_link}" if event.html_link else ""
        return (
            f"Successfully created event **'{event.summary}'**!\n"
            f"- ID: `{event.id}`\n"
            f"- Time: {event.start} to {event.end}"
            f"{meet_msg}"
            f"{link_msg}"
        )
    except Exception as exc:
        logger.error("calendar_create_event failed: %s", exc)
        raise ToolError(ToolErrorCode.EXECUTION_ERROR, f"Failed to create event: {exc}") from exc


@register_tool(
    category=ToolCategory.SCHEDULE,
    capabilities=[ToolCapability.NETWORK, ToolCapability.MUTATES_STATE],
    tags=["google_calendar", "calendar", "update"],
)
@tool(args_schema=CalendarUpdateEventInput)
async def calendar_update_event(
    event_id: str,
    summary: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
    calendar_id: str = "primary",
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg] = None,
) -> str:
    """Update or reschedule an existing event on Google Calendar."""
    user_id = _resolve_user_id(runtime)
    service = get_google_calendar_service()

    try:
        event = await service.update_event(
            event_id=event_id,
            user_id=user_id,
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            description=description,
            location=location,
            attendees=attendees,
            calendar_id=calendar_id,
        )
        return (
            f"Successfully updated event **'{event.summary}'** (ID: `{event.id}`).\n"
            f"- Time: {event.start} to {event.end}"
        )
    except Exception as exc:
        logger.error("calendar_update_event failed: %s", exc)
        raise ToolError(ToolErrorCode.EXECUTION_ERROR, f"Failed to update event: {exc}") from exc


@register_tool(
    category=ToolCategory.SCHEDULE,
    capabilities=[ToolCapability.NETWORK, ToolCapability.MUTATES_STATE],
    tags=["google_calendar", "calendar", "delete"],
)
@tool(args_schema=CalendarDeleteEventInput)
async def calendar_delete_event(
    event_id: str,
    calendar_id: str = "primary",
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg] = None,
) -> str:
    """Delete or cancel a calendar event from Google Calendar."""
    user_id = _resolve_user_id(runtime)
    service = get_google_calendar_service()

    try:
        deleted = await service.delete_event(event_id=event_id, user_id=user_id, calendar_id=calendar_id)
        if deleted:
            return f"Event with ID `{event_id}` was successfully deleted."
        return f"Event with ID `{event_id}` was not found or already deleted."
    except Exception as exc:
        logger.error("calendar_delete_event failed: %s", exc)
        raise ToolError(ToolErrorCode.EXECUTION_ERROR, f"Failed to delete event: {exc}") from exc


__all__ = [
    "calendar_list_events",
    "calendar_list_calendars",
    "calendar_get_event",
    "calendar_get_agenda",
    "calendar_check_availability",
    "calendar_create_event",
    "calendar_update_event",
    "calendar_delete_event",
]
