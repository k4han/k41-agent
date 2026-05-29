from __future__ import annotations

import os
import subprocess
from typing import Any

from agent.modules.tools import resolve_safe_path
from agent.modules.workspaces.backends import CommandResult
from agent.modules.workspaces.refs import WorkspaceRef
from agent.modules.workspaces.service import (
    IGNORED_DIR_NAMES,
    delete_workspace_entry,
    get_workspace_changes,
    get_workspace_diff,
    get_workspace_file,
    list_workspace_tree,
    rename_workspace_entry,
    resolve_workspace_root,
)

MAX_LIST_FILES_ENTRIES = 5000


class LocalWorkspaceBackend:
    """Workspace backend backed by the host filesystem."""

    def __init__(self, ref: WorkspaceRef) -> None:
        if ref.backend != "local":
            raise ValueError(f"Unsupported workspace backend: {ref.backend}")
        self.ref = ref
        self.root = resolve_workspace_root(ref.locator)

    def list_files(self, sub_dir: str = "") -> str:
        target = resolve_safe_path(str(self.root), sub_dir or ".")
        files: list[str] = []
        truncated = False
        for root, dirs, filenames in os.walk(target):
            dirs[:] = [
                directory
                for directory in dirs
                if not directory.startswith(".") and directory not in IGNORED_DIR_NAMES
            ]
            for filename in filenames:
                if len(files) >= MAX_LIST_FILES_ENTRIES:
                    truncated = True
                    break
                files.append(os.path.realpath(os.path.join(root, filename)))
            if truncated:
                break
        if not files:
            return "(Empty directory)"
        output = "\n".join(files)
        if truncated:
            output += f"\n...[truncated at {MAX_LIST_FILES_ENTRIES} entries]"
        return output

    def read_text(self, file_path: str) -> str:
        full_path = resolve_safe_path(str(self.root), file_path)
        with open(full_path, "r", encoding="utf-8") as file_handle:
            return file_handle.read()

    def write_text(self, file_path: str, content: str) -> str:
        full_path = resolve_safe_path(str(self.root), file_path)
        parent = os.path.dirname(full_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as file_handle:
            file_handle.write(content)
        return "[OK] Done"

    def execute(
        self,
        command: str,
        *,
        timeout: int = 30,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        from agent.modules.agent_runtime.active_sessions import current_session_id_var, get_active_session_registry
        session_id = current_session_id_var.get()
        registry = get_active_session_registry()

        p = subprocess.Popen(
            command,
            shell=True,
            cwd=str(self.root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        if session_id:
            registry.register_pid(session_id, p.pid)

        try:
            output, error = p.communicate(timeout=timeout)
            exit_code = p.returncode
        except subprocess.TimeoutExpired:
            p.kill()
            output, error = p.communicate()
            exit_code = -1
            error = (error or "") + f"\n[stderr]: Command timed out after {timeout} seconds."
        except Exception as exc:
            p.kill()
            output, error = p.communicate()
            exit_code = -1
            error = (error or "") + f"\n[stderr]: Command failed with exception: {exc}"
        finally:
            if session_id:
                registry.unregister_pid(session_id, p.pid)

        output_str = output or ""
        error_str = error or ""
        combined = output_str + (f"\n[stderr]: {error_str}" if error_str else "")
        truncated = False
        if max_output_chars is not None and len(combined) > max_output_chars:
            combined = combined[:max_output_chars] + "\n...[truncated]"
            truncated = True
        return CommandResult(
            output=combined,
            exit_code=exit_code,
            truncated=truncated,
        )

    def tree(self, path: str | None = None) -> dict[str, Any]:
        return list_workspace_tree(working_dir=str(self.root), path=path)

    def file(self, path: str) -> dict[str, Any]:
        return get_workspace_file(working_dir=str(self.root), path=path)

    def changes(self) -> dict[str, Any]:
        return get_workspace_changes(str(self.root))

    def diff(self, path: str) -> dict[str, Any]:
        return get_workspace_diff(working_dir=str(self.root), path=path)

    def rename(self, *, path: str, new_name: str) -> dict[str, Any]:
        return rename_workspace_entry(
            working_dir=str(self.root),
            path=path,
            new_name=new_name,
        )

    def delete(self, *, path: str) -> dict[str, Any]:
        return delete_workspace_entry(working_dir=str(self.root), path=path)


__all__ = ["LocalWorkspaceBackend"]
