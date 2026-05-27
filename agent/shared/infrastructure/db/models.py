"""Helpers for registering ORM models on shared metadata."""

from __future__ import annotations


def load_orm_models() -> tuple[type[object], ...]:
    """Import ORM models so they are attached to ``Base.metadata``."""
    from agent.modules.admin_auth import AdminCredential
    from agent.modules.agent_runtime import BackgroundTaskRecord
    from agent.modules.channels import BotSettings
    from agent.modules.conversations import ConversationThread
    from agent.modules.github import (
        GitHubInstallation,
        GitHubRepositoryBinding,
        GitHubWebhookDelivery,
    )
    from agent.modules.prompt_variables import PromptVariable
    from agent.modules.usage import LLMUsageEvent
    from agent.modules.workspaces import ThreadWorkspace
    from agent.modules.users import User
    from agent.shared.infrastructure.db.user_preferences import UserPreferences

    return (
        AdminCredential,
        BackgroundTaskRecord,
        ConversationThread,
        User,
        BotSettings,
        UserPreferences,
        GitHubInstallation,
        GitHubRepositoryBinding,
        GitHubWebhookDelivery,
        ThreadWorkspace,
        PromptVariable,
        LLMUsageEvent,
    )


__all__ = ["load_orm_models"]
