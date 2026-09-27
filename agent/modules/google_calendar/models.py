"""Data models for Google Calendar accounts and events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from agent.shared.infrastructure.db import BaseModel as DBBaseModel, utcnow


class GoogleCalendarAccount(DBBaseModel):
    """SQLAlchemy ORM model storing encrypted Google OAuth tokens."""

    __tablename__ = "google_calendar_accounts"

    user_id = Column(String(255), nullable=False, default="default", index=True)
    account_email = Column(String(255), nullable=False, default="")
    encrypted_refresh_token = Column(Text, nullable=False, default="")
    access_token = Column(Text, nullable=True)
    token_expiry = Column(DateTime(timezone=True), nullable=True)
    scopes = Column(Text, nullable=False, default="")
    is_primary = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class GoogleCalendarStatus(BaseModel):
    """Public status response for Google Calendar connection."""

    connected: bool
    email: str = ""
    configured: bool = False
    scopes: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class CalendarEventItem(BaseModel):
    """Represents a calendar event returned from Google Calendar."""

    id: str
    summary: str
    start: str
    end: str
    description: str = ""
    location: str = ""
    html_link: str = ""
    meet_link: str = ""
    attendees: list[str] = Field(default_factory=list)
    status: str = "confirmed"


__all__ = [
    "GoogleCalendarAccount",
    "GoogleCalendarStatus",
    "CalendarEventItem",
]
