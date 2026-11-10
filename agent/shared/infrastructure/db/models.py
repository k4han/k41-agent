"""Helpers for registering ORM models on shared metadata."""

from __future__ import annotations


def load_orm_models() -> tuple[type[object], ...]:
    """Import ORM models so they are attached to ``Base.metadata``."""
    from agent.modules.admin_auth import AdminCredential
    from agent.modules.browser import BrowserAccountProfile, BrowserArtifact, BrowserBinding, BrowserInvocation, BrowserProfile, BrowserProjectProfile
    from agent.modules.agent_runtime import BackgroundTaskRecord
    from agent.modules.channels import BotSettings
    from agent.modules.conversations import ConversationRunError, ConversationThread
    from agent.modules.github import (
        GitHubInstallation,
        GitHubIssueTaskClaim,
        GitHubRepositoryBinding,
        GitHubWebhookDelivery,
    )
    from agent.modules.mcp import (
        AgentMCPInstall,
        MCPCredential,
        MCPServerInstall,
    )
    from agent.modules.prompt_variables import PromptVariable
    from agent.modules.projects import Project, ProjectSecret, ProjectWorkspace, WorkspaceRuntime
    from agent.modules.scheduler import SchedulerJobRun
    from agent.modules.usage import LLMUsageEvent
    from agent.modules.workspaces import ThreadWorkspace
    from agent.modules.users import User
    from agent.modules.google_calendar import GoogleCalendarAccount
    from agent.modules.instabox import InstaboxOperation, InstaboxPortToken
    from agent.shared.infrastructure.db.runtime_settings import RuntimeSetting
    from agent.shared.infrastructure.db.user_preferences import UserPreferences

    return (
        AdminCredential,
        BrowserProfile,
        BrowserAccountProfile,
        BrowserProjectProfile,
        BrowserBinding,
        BrowserInvocation,
        BrowserArtifact,
        BackgroundTaskRecord,
        ConversationThread,
        ConversationRunError,
        User,
        BotSettings,
        UserPreferences,
        GitHubInstallation,
        GitHubIssueTaskClaim,
        GitHubRepositoryBinding,
        GitHubWebhookDelivery,
        MCPCredential,
        MCPServerInstall,
        AgentMCPInstall,
        ThreadWorkspace,
        PromptVariable,
        Project,
        ProjectSecret,
        ProjectWorkspace,
        WorkspaceRuntime,
        LLMUsageEvent,
        SchedulerJobRun,
        RuntimeSetting,
        GoogleCalendarAccount,
        InstaboxOperation,
        InstaboxPortToken,
    )


__all__ = ["load_orm_models"]
