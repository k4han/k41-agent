from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig

from agent.modules.workspaces import (
    DEFAULT_LOCAL_WORKSPACE,
    WorkspaceRef,
    normalize_workspace_ref,
)

DEFAULT_CONTEXT_TRIM_THRESHOLD = 50_000
DEFAULT_WORKING_DIR = DEFAULT_LOCAL_WORKSPACE


@dataclass(init=False)
class WorkflowContext:
    """Run-scoped context passed via LangGraph context_schema."""

    workspace: WorkspaceRef
    context_trim_threshold: int
    agent_name: str
    allowed_tool_names: list[str]
    allowed_skill_names: list[str] | None
    provider: str | None = None
    model: str | None = None

    def __init__(
        self,
        *,
        workspace: WorkspaceRef | dict[str, Any] | str | None = None,
        working_dir: str | None = None,
        context_trim_threshold: int | None = None,
        max_context_tokens: int | None = None,  # Backward compatibility
        agent_name: str = "default",
        allowed_tool_names: list[str] | None = None,
        allowed_skill_names: list[str] | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        from agent.shared.config.service import get_config_service
        default_locator = str(get_config_service().get_path("workspace.root", "~/k41-agent"))
        self.workspace = normalize_workspace_ref(
            workspace if workspace is not None else working_dir,
            default_locator=default_locator,
        )
        self.context_trim_threshold = (
            context_trim_threshold 
            if context_trim_threshold is not None 
            else (max_context_tokens if max_context_tokens is not None else DEFAULT_CONTEXT_TRIM_THRESHOLD)
        )
        self.agent_name = agent_name
        self.allowed_tool_names = list(allowed_tool_names or [])
        self.allowed_skill_names = (
            None if allowed_skill_names is None else list(allowed_skill_names)
        )
        self.provider = provider
        self.model = model

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

        For Daytona/Modal/OpenShell sandboxes the ``locator`` is a sandbox ID and is not
        a usable filesystem path. Prefer ``metadata["root"]`` so the value
        reflects the actual cwd used by the workspace backend (which may sit
        inside a cloned repository).
        """
        if self.workspace.backend in {"daytona", "modal", "openshell"}:
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

    def get_context_trim_threshold(self) -> int:
        """Get context trim threshold from context."""
        return self.context_trim_threshold

    def get_max_context_tokens(self) -> int:
        """Get max context tokens from context (backward compatibility)."""
        return self.context_trim_threshold

    @property
    def max_context_tokens(self) -> int:
        """Get max context tokens property (backward compatibility)."""
        return self.context_trim_threshold


def make_context(
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    working_dir: str | None = None,
    context_trim_threshold: int | None = None,
    max_context_tokens: int | None = None,  # Backward compatibility
    agent_name: str = "default",
    allowed_tool_names: list[str] | None = None,
    allowed_skill_names: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> WorkflowContext:
    """Create a runtime context payload for a graph run."""
    from agent.modules.tools import get_default_tool_names

    if allowed_tool_names is None:
        allowed_tool_names = get_default_tool_names()

    from agent.shared.config.service import get_config_service
    default_locator = str(get_config_service().get_path("workspace.root", "~/k41-agent"))
    resolved_workspace = normalize_workspace_ref(
        workspace if workspace is not None else working_dir,
        default_locator=default_locator,
    )

    return WorkflowContext(
        workspace=resolved_workspace,
        context_trim_threshold=context_trim_threshold,
        max_context_tokens=max_context_tokens,
        agent_name=agent_name,
        allowed_tool_names=allowed_tool_names,
        allowed_skill_names=allowed_skill_names,
        provider=provider.strip() if provider else None,
        model=model.strip() if model else None,
    )


def make_config(
    thread_id: str,
    recursion_limit: int | None = None,
) -> RunnableConfig:
    """Create runnable config used by checkpointing and recursion control."""
    if recursion_limit is None:
        from agent.shared.config.service import get_config_service
        recursion_limit = get_config_service().get_int("recursion_limit", 100)
    return {
        "configurable": {
            "thread_id": thread_id,
        },
        "recursion_limit": recursion_limit,
    }

