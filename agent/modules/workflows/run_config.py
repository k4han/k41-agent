from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig

from agent.modules.workspaces import WorkspaceRef, normalize_workspace_ref
from agent.shared.config.constants import DEFAULT_WORKSPACE_ROOT

DEFAULT_CONTEXT_COMPACT_THRESHOLD = 75
DEFAULT_WORKING_DIR = DEFAULT_WORKSPACE_ROOT


@dataclass(init=False)
class WorkflowContext:
    """Run-scoped context passed via LangGraph context_schema."""

    workspace: WorkspaceRef
    context_compact_threshold: int | None
    channel_context_trim_threshold: int | None
    channel_trim_applied: bool
    agent_name: str
    allowed_tool_names: list[str]
    allowed_skill_names: list[str] | None
    provider: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None

    def __init__(
        self,
        *,
        workspace: WorkspaceRef | dict[str, Any] | str | None = None,
        working_dir: str | None = None,
        context_compact_threshold: int | None = None,
        channel_context_trim_threshold: int | None = None,
        agent_name: str = "default",
        allowed_tool_names: list[str] | None = None,
        allowed_skill_names: list[str] | None = None,
        provider: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        from agent.shared.config.service import get_config_service
        default_locator = str(
            get_config_service().get_path("workspace.root", DEFAULT_WORKSPACE_ROOT)
        )
        self.workspace = normalize_workspace_ref(
            workspace if workspace is not None else working_dir,
            default_locator=default_locator,
        )
        if context_compact_threshold is not None and (
            type(context_compact_threshold) is not int or not 1 <= context_compact_threshold <= 100
        ):
            raise ValueError("context_compact_threshold must be an integer between 1 and 100.")
        if channel_context_trim_threshold is not None and (
            type(channel_context_trim_threshold) is not int or channel_context_trim_threshold < 1
        ):
            raise ValueError("channel_context_trim_threshold must be a positive integer.")
        self.context_compact_threshold = context_compact_threshold
        self.channel_context_trim_threshold = channel_context_trim_threshold
        self.channel_trim_applied = False
        self.agent_name = agent_name
        self.allowed_tool_names = list(allowed_tool_names or [])
        self.allowed_skill_names = (
            None if allowed_skill_names is None else list(allowed_skill_names)
        )
        self.provider = provider
        self.model = model
        self.reasoning_effort = reasoning_effort

    def get_agent_name(self) -> str:
        """Get agent name from context."""
        return self.agent_name

    def get_model(self) -> str | None:
        """Get run-scoped model override from context."""
        return self.model

    def get_provider(self) -> str | None:
        """Get run-scoped provider override from context."""
        return self.provider

    def get_working_dir(self) -> str:
        """Get working directory from context.

        For Daytona/Modal sandboxes the ``locator`` is a sandbox ID and is not
        a usable filesystem path. Prefer ``metadata["root"]`` so the value
        reflects the actual cwd used by the workspace backend (which may sit
        inside a cloned repository).
        """
        if self.workspace.backend in {"daytona", "modal"}:
            root = str(self.workspace.metadata.get("root") or "").strip()
            if root:
                return root
        return self.workspace.locator

    def get_workspace(self) -> WorkspaceRef:
        """Get workspace reference from context."""
        return self.workspace

    def get_allowed_tool_names(self) -> list[str]:
        """Get allowed tool names from context."""
        return self.allowed_tool_names

    def get_allowed_skill_names(self) -> list[str] | None:
        """Get run-scoped global skill whitelist."""
        return self.allowed_skill_names

    def get_context_compact_threshold(self) -> int | None:
        """Return the run override, or None to inherit the executing agent card."""
        return self.context_compact_threshold


def make_context(
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    working_dir: str | None = None,
    context_compact_threshold: int | None = None,
    channel_context_trim_threshold: int | None = None,
    agent_name: str = "default",
    allowed_tool_names: list[str] | None = None,
    allowed_skill_names: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> WorkflowContext:
    """Create a runtime context payload for a graph run."""
    from agent.modules.tools import get_default_tool_names

    if allowed_tool_names is None:
        allowed_tool_names = get_default_tool_names()

    from agent.shared.config.service import get_config_service
    default_locator = str(
        get_config_service().get_path("workspace.root", DEFAULT_WORKSPACE_ROOT)
    )
    resolved_workspace = normalize_workspace_ref(
        workspace if workspace is not None else working_dir,
        default_locator=default_locator,
    )

    return WorkflowContext(
        workspace=resolved_workspace,
        context_compact_threshold=context_compact_threshold,
        channel_context_trim_threshold=channel_context_trim_threshold,
        agent_name=agent_name,
        allowed_tool_names=allowed_tool_names,
        allowed_skill_names=allowed_skill_names,
        provider=provider.strip() if provider else None,
        model=model.strip() if model else None,
        reasoning_effort=reasoning_effort,
    )


def make_config(
    thread_id: str,
    recursion_limit: int | None = None,
) -> RunnableConfig:
    """Create runnable config used by checkpointing and recursion control."""
    if recursion_limit is None:
        from agent.shared.config.service import get_config_service
        recursion_limit = get_config_service().get_int("recursion_limit", 100)
    from agent.shared.thread_ids import resolve_thread_id, storage_thread_id

    thread_id = resolve_thread_id(thread_id)
    return {
        "configurable": {
            "thread_id": thread_id,
            "approval_supported": storage_thread_id(thread_id).startswith("api_"),
        },
        "recursion_limit": recursion_limit,
    }

