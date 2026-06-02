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

    def list_files(self, sub_dir: str = "") -> str:
        ...

    def read_text(self, file_path: str) -> str:
        ...

    def write_text(self, file_path: str, content: str) -> str:
        ...

    def execute(
        self,
        command: str,
        *,
        timeout: int = 30,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        ...

    def tree(self, path: str | None = None) -> dict[str, Any]:
        ...

    def file(self, path: str) -> dict[str, Any]:
        ...

    def changes(self) -> dict[str, Any]:
        ...

    def diff(self, path: str) -> dict[str, Any]:
        ...

    def rename(self, *, path: str, new_name: str) -> dict[str, Any]:
        ...

    def delete(self, *, path: str) -> dict[str, Any]:
        ...


__all__ = ["CommandResult", "WorkspaceBackend", "WorkspaceUnavailableError"]
