import os
from pathlib import Path
from typing import TypeVar

from langchain_core.runnables import RunnableConfig
from typing_extensions import TypedDict

DEFAULT_MAX_CONTEXT_TOKENS = 50_000
DEFAULT_WORKING_DIR = str(Path.home() / "kaka-agent")

T = TypeVar("T")


def get_context_value(runtime_or_context, key: str, default: T) -> T:
    """Safely extract a key from runtime.context.

    Supports both dict-like (TypedDict) and object-like contexts.
    """
    raw_context = runtime_or_context or {}
    if isinstance(raw_context, dict):
        return raw_context.get(key, default)
    return getattr(raw_context, key, default)


class WorkflowContext(TypedDict):
    """Run-scoped context passed via LangGraph context_schema."""

    service_type: str
    working_dir: str
    max_context_tokens: int
    agent_name: str
    allowed_tool_names: list[str]


def make_context(
    service_type: str = "default",
    working_dir: str | None = None,
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    agent_name: str = "default",
    allowed_tool_names: list[str] | None = None,
) -> WorkflowContext:
    """Create a runtime context payload for a graph run."""
    from agent.modules.workflows.infrastructure.langgraph.tools.registry import (
        get_default_tool_names,
    )

    if allowed_tool_names is None:
        allowed_tool_names = get_default_tool_names()

    # Allow working_dir override, otherwise use default
    resolved_dir = working_dir or DEFAULT_WORKING_DIR

    return {
        "service_type": service_type,
        "working_dir": resolved_dir,
        "max_context_tokens": max_context_tokens,
        "agent_name": agent_name,
        "allowed_tool_names": allowed_tool_names,
    }


def make_config(
    thread_id: str,
    recursion_limit: int = 100,
) -> RunnableConfig:
    """Create runnable config used by checkpointing and recursion control."""
    return {
        "configurable": {
            "thread_id": thread_id,
        },
        "recursion_limit": recursion_limit,
    }
