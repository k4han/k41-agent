"""Runtime context utilities for tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")


def get_context_value(runtime_or_context, key: str, default: T) -> T:
    """Safely extract a key from runtime.context.

    Supports both dict-like (TypedDict) and object-like contexts.
    """
    raw_context = runtime_or_context or {}
    if isinstance(raw_context, dict):
        return raw_context.get(key, default)
    return getattr(raw_context, key, default)


@dataclass(frozen=True)
class ToolContext:
    """Normalized view over runtime data a tool typically needs.

    Use ``ToolContext.from_runtime(runtime)`` to populate from a LangGraph
    ``ToolRuntime`` without each tool re-parsing fields by hand.
    """

    agent_name: str = "default"
    workspace: Any = None
    working_dir: Any = None
    thread_id: str | None = None
    provider: str | None = None
    model: str | None = None

    @property
    def workspace_or_dir(self) -> Any:
        return self.workspace if self.workspace is not None else self.working_dir

    @classmethod
    def from_runtime(cls, runtime: Any) -> "ToolContext":
        raw_context = getattr(runtime, "context", None)
        thread_id: str | None = None
        config = getattr(runtime, "config", None)
        if isinstance(config, dict):
            configurable = config.get("configurable", {})
            if isinstance(configurable, dict):
                raw_tid = configurable.get("thread_id")
                if raw_tid is not None:
                    thread_id = str(raw_tid)
        return cls(
            agent_name=get_context_value(raw_context, "agent_name", "default"),
            workspace=get_context_value(raw_context, "workspace", None),
            working_dir=get_context_value(raw_context, "working_dir", None),
            thread_id=thread_id,
            provider=get_context_value(raw_context, "provider", None),
            model=get_context_value(raw_context, "model", None),
        )


__all__ = [
    "ToolContext",
    "get_context_value",
]
