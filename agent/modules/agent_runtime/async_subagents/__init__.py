"""Background subagents backed by the application's database and LangGraph."""

from agent.modules.agent_runtime.async_subagents.models import (
    SubagentExecutionRecord, SubagentNotificationRecord, SubagentTaskRecord,
)


def get_async_subagent_service(container=None):
    from agent.bootstrap.container import require_active_container

    return require_active_container(container).async_subagents


def active_async_subagent_service():
    from agent.bootstrap.container import get_active_container

    container = get_active_container()
    return getattr(container, "_async_subagents", None) if container is not None else None


__all__ = ["SubagentExecutionRecord", "SubagentNotificationRecord", "SubagentTaskRecord",
           "get_async_subagent_service", "active_async_subagent_service"]
