"""Database migrations for Google Calendar integration."""

from __future__ import annotations

import logging

from sqlalchemy import create_engine

from agent.modules.google_calendar.models import GoogleCalendarAccount
from agent.shared.infrastructure.db import Base
from agent.shared.infrastructure.db.engine import _normalize_url_to_sync

logger = logging.getLogger(__name__)


def migrate_google_calendar_tables(database_url: str) -> None:
    """Ensure Google Calendar account tables exist in the database."""
    engine = create_engine(_normalize_url_to_sync(database_url), echo=False)
    try:
        GoogleCalendarAccount.__table__.create(engine, checkfirst=True)
    except Exception as exc:
        logger.warning("Could not auto-create google_calendar_accounts table: %s", exc)
    finally:
        engine.dispose()


__all__ = ["migrate_google_calendar_tables"]
