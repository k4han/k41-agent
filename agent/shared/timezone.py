from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent.shared.config.constants import (
    DEFAULT_DISPLAY_TIMEZONE,
    DISPLAY_TIMEZONE_CONFIG_KEY,
)
from agent.shared.config.service import get_config_service

logger = logging.getLogger(__name__)


def resolve_display_timezone() -> tuple[str, ZoneInfo]:
    configured = get_config_service().get_str(
        DISPLAY_TIMEZONE_CONFIG_KEY,
        DEFAULT_DISPLAY_TIMEZONE,
    ).strip() or DEFAULT_DISPLAY_TIMEZONE
    try:
        return configured, ZoneInfo(configured)
    except ZoneInfoNotFoundError:
        logger.warning(
            "Invalid display timezone '%s'; falling back to %s.",
            configured,
            DEFAULT_DISPLAY_TIMEZONE,
        )
        return DEFAULT_DISPLAY_TIMEZONE, ZoneInfo(DEFAULT_DISPLAY_TIMEZONE)


def display_now() -> datetime:
    _, zone = resolve_display_timezone()
    return datetime.now(zone)


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def localize_iso_datetime(value: Any, tz: ZoneInfo) -> str | None:
    if not value:
        return None
    try:
        parsed = parse_iso_datetime(str(value))
    except ValueError:
        return str(value)
    return parsed.astimezone(tz).isoformat()


__all__ = [
    "display_now",
    "ensure_aware_utc",
    "localize_iso_datetime",
    "parse_iso_datetime",
    "resolve_display_timezone",
]
