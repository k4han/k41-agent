from __future__ import annotations

from typing import Any

from agent.modules.workspaces.backends import CommandResult, WorkspaceBackend
from agent.modules.workspaces.refs import WorkspaceRef


class VirtualWorkspaceBackend:
    """Workspace backend proxy that translates virtual paths to physical paths."""

    def __init__(self, inner_backend: WorkspaceBackend, virtual_name: str = "workspace") -> None:
        self.inner_backend = inner_backend
        self.ref = inner_backend.ref
        self.virtual_name = virtual_name.strip("/")
        self.virtual_prefix = f"/{self.virtual_name}/"

    def _to_relative_path(self, virtual_path: str) -> str:
        """Translate a virtual path like /workspace/src/main.py to src/main.py."""
        normalized = virtual_path.replace("\\", "/").strip()
        if normalized.startswith(self.virtual_prefix):
            return normalized[len(self.virtual_prefix):].lstrip("/")
        if normalized.startswith(self.virtual_prefix.rstrip("/")):
            return normalized[len(self.virtual_prefix.rstrip("/")):].lstrip("/")
        return normalized.lstrip("/")

    def _to_virtual_path(self, relative_path: str) -> str:
        """Translate a relative path like src/main.py to /workspace/src/main.py."""
        clean_rel = relative_path.replace("\\", "/").lstrip("/")
        if not clean_rel:
            return self.virtual_prefix
        return f"{self.virtual_prefix}{clean_rel}"

    def list_files(self, sub_dir: str = "") -> str:
        rel_sub_dir = self._to_relative_path(sub_dir)
        raw_output = self.inner_backend.list_files(rel_sub_dir)
        if raw_output == "(Empty directory)":
            return raw_output
        
        if rel_sub_dir:
            virtual_prefix_with_subdir = f"{self.virtual_prefix}{rel_sub_dir.lstrip('/')}/"
        else:
            virtual_prefix_with_subdir = self.virtual_prefix

        lines = raw_output.splitlines()
        virtual_lines = []
        for line in lines:
            if line.startswith("...[truncated at"):
                virtual_lines.append(line)
            else:
                clean_line = line.replace("\\", "/").lstrip("/")
                virtual_lines.append(f"{virtual_prefix_with_subdir}{clean_line}")
        return "\n".join(virtual_lines)

    def read_text(self, file_path: str) -> str:
        rel_path = self._to_relative_path(file_path)
        return self.inner_backend.read_text(rel_path)

    def write_text(self, file_path: str, content: str) -> str:
        rel_path = self._to_relative_path(file_path)
        raw_output = self.inner_backend.write_text(rel_path, content)
        # Prevent absolute path leak in output message
        locator = self.ref.locator
        clean_output = raw_output.replace(locator, self.virtual_prefix.rstrip("/"))
        clean_output = clean_output.replace(locator.replace("\\", "/"), self.virtual_prefix.rstrip("/"))
        clean_output = clean_output.replace("\\", "/")
        return clean_output

    def execute(
        self,
        command: str,
        *,
        timeout: int = 30,
        max_output_chars: int | None = None,
    ) -> CommandResult:
        # Translate virtual prefix to relative path or dot in command string
        virtual_path_with_slash = self.virtual_prefix
        virtual_path_no_slash = self.virtual_prefix.rstrip("/")

        translated_cmd = command
        translated_cmd = translated_cmd.replace(virtual_path_with_slash, "")
        translated_cmd = translated_cmd.replace(virtual_path_no_slash, ".")

        result = self.inner_backend.execute(
            translated_cmd,
            timeout=timeout,
            max_output_chars=max_output_chars,
        )

        # Sanitize stdout/stderr to hide absolute paths
        clean_output = result.output
        locator = self.ref.locator
        clean_output = clean_output.replace(locator, virtual_path_no_slash)
        clean_output = clean_output.replace(locator.replace("\\", "/"), virtual_path_no_slash)

        return CommandResult(
            output=clean_output,
            exit_code=result.exit_code,
            truncated=result.truncated,
        )

    def tree(self, path: str | None = None) -> dict[str, Any]:
        rel_path = self._to_relative_path(path or "")
        res = self.inner_backend.tree(rel_path or None)
        res["root"] = self.virtual_prefix.rstrip("/")
        if "path" in res:
            res["path"] = self._to_virtual_path(res["path"]) if res["path"] else ""
        if "entries" in res:
            for entry in res["entries"]:
                if "path" in entry:
                    entry["path"] = self._to_virtual_path(entry["path"])
        return res

    def file(self, path: str) -> dict[str, Any]:
        rel_path = self._to_relative_path(path)
        res = self.inner_backend.file(rel_path)
        res["root"] = self.virtual_prefix.rstrip("/")
        if "path" in res:
            res["path"] = self._to_virtual_path(res["path"])
        return res

    def changes(self) -> dict[str, Any]:
        res = self.inner_backend.changes()
        res["root"] = self.virtual_prefix.rstrip("/")
        if "changes" in res:
            for change in res["changes"]:
                if "path" in change:
                    change["path"] = self._to_virtual_path(change["path"])
                if "old_path" in change:
                    change["old_path"] = self._to_virtual_path(change["old_path"])
        return res

    def diff(self, path: str) -> dict[str, Any]:
        rel_path = self._to_relative_path(path)
        res = self.inner_backend.diff(rel_path)
        res["root"] = self.virtual_prefix.rstrip("/")
        if "path" in res:
            res["path"] = self._to_virtual_path(res["path"])
        if "diff" in res and isinstance(res["diff"], str):
            locator = self.ref.locator
            res["diff"] = res["diff"].replace(locator, self.virtual_prefix.rstrip("/"))
            res["diff"] = res["diff"].replace(locator.replace("\\", "/"), self.virtual_prefix.rstrip("/"))
        return res

    def rename(self, *, path: str, new_name: str) -> dict[str, Any]:
        rel_path = self._to_relative_path(path)
        res = self.inner_backend.rename(path=rel_path, new_name=new_name)
        res["root"] = self.virtual_prefix.rstrip("/")
        if "path" in res:
            res["path"] = self._to_virtual_path(res["path"])
        if "new_path" in res:
            res["new_path"] = self._to_virtual_path(res["new_path"])
        return res

    def delete(self, *, path: str) -> dict[str, Any]:
        rel_path = self._to_relative_path(path)
        res = self.inner_backend.delete(path=rel_path)
        res["root"] = self.virtual_prefix.rstrip("/")
        if "path" in res:
            res["path"] = self._to_virtual_path(res["path"])
        return res


__all__ = ["VirtualWorkspaceBackend"]
