"""Public facade for the tools module.

Other modules should import from here, not from internal packages.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

from langchain_core.tools import BaseTool

from agent.modules.tools.domain import (
    ToolCapability,
    ToolCategory,
    ToolDescriptor,
    ToolSource,
)
from agent.modules.tools.policy import ToolPolicy
from agent.modules.tools.registry_service import (
    ensure_mcp_loaded,
    get_registry_service,
    reload_mcp_descriptors,
)
from agent.modules.tools.resolver import ToolResolver
from agent.modules.tools.result import (
    ToolError,
    ToolErrorCode,
    format_tool_error,
)
from agent.modules.tools.builtin.utility.plan_mode import (
    PLAN_MODE_TOOL_NAME,
    PLAN_REVIEW_APPROVED_PREFIX,
    PLAN_REVIEW_INTERRUPT_TYPE,
    PLAN_REVIEW_REVISION_PREFIX,
    PlanModeResumePayload,
)
from agent.modules.tools.builtin.utility.plan_resume import PlanResumePayload
from agent.modules.tools.builtin.utility.ask_user import (
    ASK_USER_INTERRUPT_TYPE,
    ASK_USER_TOOL_NAME,
    AskUserAnswerResumePayload,
    AskUserFreeText,
    AskUserInput,
    AskUserOption,
    AskUserQuestion,
    HumanResumePayload,
    UserQuestionAnswer,
)
from agent.modules.tools.runtime.context import (
    ToolContext,
    get_context_value,
    get_thread_id,
)
from agent.modules.tools.runtime.path_guard import resolve_safe_path

T = TypeVar("T")


def get_tool_by_name(name: str) -> BaseTool | None:
    """Get a tool instance by its string name."""
    service = get_registry_service()
    return service.get_tool_by_name(name)


def get_default_tools() -> list[BaseTool]:
    """Return the default set of tools."""
    service = get_registry_service()
    return service.get_all_tools()


def resolve_tools(tool_names: Iterable[str]) -> list[BaseTool]:
    """Resolve tools by name, skipping unknown names."""
    service = get_registry_service()
    return service.resolve_tools(list(tool_names))


def get_default_tool_names() -> list[str]:
    """Return the names of all default tools."""
    service = get_registry_service()
    return service.get_tool_names()


def get_runtime_context_value(runtime_or_context, key: str, default: T) -> T:
    """Read a value from runtime context. Public wrapper to avoid importing from infrastructure."""
    return get_context_value(runtime_or_context, key, default)


def close_thread_shell_sessions(thread_id: str) -> int:
    """Close local and remote shell sessions for a thread tree."""
    from agent.modules.tools.builtin.shell.daytona_session_manager import (
        daytona_session_manager,
    )
    from agent.modules.tools.builtin.shell.modal_session_manager import (
        modal_session_manager,
    )
    from agent.modules.tools.builtin.shell.session_manager import session_manager

    return (
        session_manager.close_thread_sessions(thread_id)
        + daytona_session_manager.close_thread_sessions(thread_id)
        + modal_session_manager.close_thread_sessions(thread_id)
    )


def get_default_descriptors() -> list[ToolDescriptor]:
    """Return all descriptors loaded into the registry."""
    return get_registry_service().get_descriptors()


def find_tools(
    *,
    category: ToolCategory | None = None,
    source: ToolSource | None = None,
    capabilities: Iterable[ToolCapability] | None = None,
    any_capabilities: Iterable[ToolCapability] | None = None,
    tags: Iterable[str] | None = None,
) -> list[BaseTool]:
    """Filter tools by category / source / capability / tag."""
    service = get_registry_service()
    return service.find_tools(
        category=category,
        source=source,
        capabilities=list(capabilities) if capabilities else None,
        any_capabilities=list(any_capabilities) if any_capabilities else None,
        tags=list(tags) if tags else None,
    )


def find_descriptors(
    *,
    category: ToolCategory | None = None,
    source: ToolSource | None = None,
    capabilities: Iterable[ToolCapability] | None = None,
    any_capabilities: Iterable[ToolCapability] | None = None,
    tags: Iterable[str] | None = None,
) -> list[ToolDescriptor]:
    """Filter descriptors by category / source / capability / tag."""
    service = get_registry_service()
    return service.find(
        category=category,
        source=source,
        capabilities=list(capabilities) if capabilities else None,
        any_capabilities=list(any_capabilities) if any_capabilities else None,
        tags=list(tags) if tags else None,
    )


def resolve_tools_for_agent(agent_name: str) -> list[BaseTool]:
    """Synchronously resolve tools (built-in only) for the given agent name."""
    return ToolResolver().resolve_for_agent(agent_name)


async def aresolve_tools_for_agent(agent_name: str) -> list[BaseTool]:
    """Asynchronously resolve tools (built-in + MCP) for the given agent name."""
    return await ToolResolver().aresolve_for_agent(agent_name)


__all__ = [
    "ToolCapability",
    "ToolCategory",
    "ToolContext",
    "ToolDescriptor",
    "ToolError",
    "ToolErrorCode",
    "ToolPolicy",
    "ToolResolver",
    "ToolSource",
    "PLAN_MODE_TOOL_NAME",
    "PLAN_REVIEW_APPROVED_PREFIX",
    "PLAN_REVIEW_INTERRUPT_TYPE",
    "PLAN_REVIEW_REVISION_PREFIX",
    "ASK_USER_INTERRUPT_TYPE",
    "ASK_USER_TOOL_NAME",
    "AskUserAnswerResumePayload",
    "AskUserFreeText",
    "AskUserInput",
    "AskUserOption",
    "AskUserQuestion",
    "HumanResumePayload",
    "PlanModeResumePayload",
    "PlanResumePayload",
    "UserQuestionAnswer",
    "aresolve_tools_for_agent",
    "close_thread_shell_sessions",
    "ensure_mcp_loaded",
    "reload_mcp_descriptors",
    "find_descriptors",
    "find_tools",
    "format_tool_error",
    "get_default_descriptors",
    "get_default_tool_names",
    "get_default_tools",
    "get_runtime_context_value",
    "get_thread_id",
    "get_tool_by_name",
    "resolve_safe_path",
    "resolve_tools",
    "resolve_tools_for_agent",
]
