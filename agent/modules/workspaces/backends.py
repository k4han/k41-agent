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


class WorkspaceBackend(Protocol):
    ref: WorkspaceRef

    async def list_files(self, sub_dir: str = "") -> str:
        ...

    async def read_text(self, file_path: str) -> str:
        ...

    async def write_text(self, file_path: str, content: str) -> str:
        ...

    async def execute(
        self,
        command: str,
        *,
        timeout: int = 30,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        ...

    async def tree(self, path: str | None = None) -> dict[str, Any]:
        ...

    async def file(self, path: str) -> dict[str, Any]:
        ...

    async def changes(self) -> dict[str, Any]:
        ...

    async def diff(self, path: str) -> dict[str, Any]:
        ...

    async def rename(self, *, path: str, new_name: str) -> dict[str, Any]:
        ...

    async def delete(self, *, path: str) -> dict[str, Any]:
        ...


__all__ = ["CommandResult", "WorkspaceBackend", "WorkspaceUnavailableError"]
