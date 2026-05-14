from dataclasses import dataclass
from pathlib import Path

from langchain_core.runnables import RunnableConfig

DEFAULT_MAX_CONTEXT_TOKENS = 50_000
DEFAULT_WORKING_DIR = str(Path.home() / "kaka-agent")


@dataclass
class WorkflowContext:
    """Run-scoped context passed via LangGraph context_schema."""

    working_dir: str
    max_context_tokens: int
    agent_name: str
    allowed_tool_names: list[str]
    model: str | None = None

    def get_agent_name(self) -> str:
        """Get agent name from context."""
        return self.agent_name

    def get_model(self) -> str | None:
        """Get run-scoped model override from context."""
        return self.model

    def get_working_dir(self) -> str:
        """Get working directory from context."""
        return self.working_dir

    def get_allowed_tool_names(self) -> list[str]:
        """Get allowed tool names from context."""
        return self.allowed_tool_names

    def get_max_context_tokens(self) -> int:
        """Get max context tokens from context."""
        return self.max_context_tokens


def make_context(
    working_dir: str | None = None,
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    agent_name: str = "default",
    allowed_tool_names: list[str] | None = None,
    model: str | None = None,
) -> WorkflowContext:
    """Create a runtime context payload for a graph run."""
    from agent.modules.tools import get_default_tool_names

    if allowed_tool_names is None:
        allowed_tool_names = get_default_tool_names()

    # Allow working_dir override, otherwise use default
    resolved_dir = working_dir or DEFAULT_WORKING_DIR

    return WorkflowContext(
        working_dir=resolved_dir,
        max_context_tokens=max_context_tokens,
        agent_name=agent_name,
        allowed_tool_names=allowed_tool_names,
        model=model.strip() if model else None,
    )


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
