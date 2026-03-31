import os
from typing import TypeVar

from langchain_core.runnables import RunnableConfig
from typing_extensions import TypedDict

DEFAULT_MAX_CONTEXT_TOKENS = 50_000

T = TypeVar("T")


def get_context_value(
    runtime_or_context, key: str, default: T
) -> T:
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


def make_context(
    service_type: str = "default",
    working_dir: str | None = None,
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
) -> WorkflowContext:
    """Create a runtime context payload for a graph run."""
    return {
        "service_type": service_type,
        "working_dir": working_dir or os.getcwd(),
        "max_context_tokens": max_context_tokens,
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
