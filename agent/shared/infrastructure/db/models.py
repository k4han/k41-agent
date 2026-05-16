"""Helpers for registering ORM models on shared metadata."""

from __future__ import annotations


def load_orm_models() -> tuple[type[object], ...]:
    """Import ORM models so they are attached to ``Base.metadata``."""
    from agent.modules.admin_auth import AdminCredential
    from agent.modules.channels import BotSettings
    from agent.modules.github import (
        GitHubInstallation,
        GitHubRepositoryBinding,
        GitHubWebhookDelivery,
    )
    from agent.modules.users import User
    from agent.shared.infrastructure.db.user_preferences import UserPreferences

    return (
        AdminCredential,
        User,
        BotSettings,
        UserPreferences,
        GitHubInstallation,
        GitHubRepositoryBinding,
        GitHubWebhookDelivery,
    )


__all__ = ["load_orm_models"]
