"""Database migrations for Google Calendar integration."""

from __future__ import annotations

import logging

from agent.modules.google_calendar.models import GoogleCalendarAccount
from agent.shared.infrastructure.db import Base
from agent.shared.infrastructure.db.engine import create_sync_engine as create_engine

logger = logging.getLogger(__name__)


def migrate_google_calendar_tables(database_url: str) -> None:
    """Ensure Google Calendar account tables exist in the database."""
    engine = create_engine(database_url, echo=False)
    try:
        GoogleCalendarAccount.__table__.create(engine, checkfirst=True)
    except Exception as exc:
        logger.warning("Could not auto-create google_calendar_accounts table: %s", exc)
    finally:
        engine.dispose()


__all__ = ["migrate_google_calendar_tables"]
