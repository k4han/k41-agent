"""Helpers for registering ORM models on shared metadata."""

from __future__ import annotations


def load_orm_models() -> tuple[type[object], type[object], type[object]]:
    """Import ORM models so they are attached to ``Base.metadata``."""
    from agent.modules.channels.infrastructure.models import BotSettings
    from agent.modules.settings.infrastructure.models import UserPreferences
    from agent.modules.users.infrastructure.models import User

    return User, BotSettings, UserPreferences


__all__ = ["load_orm_models"]
