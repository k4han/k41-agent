from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from agent.modules.workspaces.refs import WorkspaceRef


@dataclass(frozen=True, slots=True)
class CommandResult:
    output: str
    exit_code: int | None = None
    truncated: bool = False


class WorkspaceUnavailableError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        backend: str | None = None,
        locator: str | None = None,
    ) -> None:
        super().__init__(message)
        self.backend = backend
        self.locator = locator


class UnsupportedWorkspaceCapabilityError(RuntimeError):
    def __init__(
        self,
        *,
        backend: str,
        capability: str,
        locator: str | None = None,
    ) -> None:
        message = (
            f"Workspace backend '{backend}' does not support "
            f"the {capability} capability."
        )
        super().__init__(message)
        self.backend = backend
        self.capability = capability
        self.locator = locator


class WorkspaceFileIO(Protocol):
    ref: WorkspaceRef

    async def list_dir(self, path: str = "") -> str:
        ...

    async def read_text(self, file_path: str) -> str:
        ...

    async def write_text(self, file_path: str, content: str) -> str:
        ...


class WorkspaceCommandExecutor(Protocol):
    ref: WorkspaceRef

    async def execute(
        self,
        command: str,
        *,
        timeout: int = 30,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        ...


class WorkspaceBrowser(Protocol):
    ref: WorkspaceRef

    async def tree(self, path: str | None = None) -> dict[str, Any]:
        ...

    async def file(self, path: str) -> dict[str, Any]:
        ...


class WorkspaceChangeInspector(Protocol):
    ref: WorkspaceRef

    async def changes(self) -> dict[str, Any]:
        ...

    async def diff(self, path: str) -> dict[str, Any]:
        ...


class WorkspaceEntryMutator(Protocol):
    ref: WorkspaceRef

    async def rename(self, *, path: str, new_name: str) -> dict[str, Any]:
        ...

    async def delete(self, *, path: str) -> dict[str, Any]:
        ...


class WorkspaceRepositoryCloner(Protocol):
    ref: WorkspaceRef

    async def clone_repository(
        self,
        *,
        owner: str,
        repo: str,
        default_branch: str = "main",
        token: str | None = None,
        depth: int = 1,
    ) -> str:
        ...


class WorkspaceLifecycleManager(Protocol):
    ref: WorkspaceRef

    async def delete_workspace(self) -> str:
        ...


class DaytonaWorkspaceLifecycleManager(WorkspaceLifecycleManager, Protocol):
    async def stop_workspace(self, *, force: bool = False) -> str:
        ...

    async def archive_workspace(self) -> str:
        ...


__all__ = [
    "CommandResult",
    "DaytonaWorkspaceLifecycleManager",
    "UnsupportedWorkspaceCapabilityError",
    "WorkspaceBrowser",
    "WorkspaceChangeInspector",
    "WorkspaceCommandExecutor",
    "WorkspaceEntryMutator",
    "WorkspaceFileIO",
    "WorkspaceLifecycleManager",
    "WorkspaceRepositoryCloner",
    "WorkspaceUnavailableError",
]
