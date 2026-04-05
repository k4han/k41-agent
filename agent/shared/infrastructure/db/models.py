"""Helpers for registering ORM models on shared metadata."""

from __future__ import annotations


def load_orm_models() -> tuple[type[object], type[object], type[object]]:
    """Import ORM models so they are attached to ``Base.metadata``."""
    from agent.modules.channels.public import BotSettings
    from agent.modules.users.public import User
    from agent.shared.infrastructure.db.user_preferences import UserPreferences

    return User, BotSettings, UserPreferences


__all__ = ["load_orm_models"]
