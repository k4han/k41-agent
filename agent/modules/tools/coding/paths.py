"""Filesystem path confinement shared by local and sandbox execution."""

from __future__ import annotations

import os
from pathlib import Path

from agent.modules.tools.coding.contracts import CodingError, InvocationContext


class PathPermissions:
    def resolve_path(self, context: InvocationContext, value: str, action: str,
                     *, authorize: bool = True, allow_interrupt: bool = True) -> Path:
        base = Path(context.workspace).resolve()
        requested = Path(value or ".").expanduser()
        target = (requested if requested.is_absolute() else base / requested).resolve()
        try:
            inside = os.path.commonpath([os.path.normcase(base), os.path.normcase(target)]) == os.path.normcase(base)
        except ValueError:
            inside = False
        if not inside:
            if not requested.is_absolute():
                raise CodingError("permission_denied", "Relative paths and symlinks may not escape the workspace.")
            if authorize:
                self.assert_allowed(context, "external_directory", str(target.parent if action != "shell" else target), allow_interrupt=allow_interrupt)
        if authorize:
            self.assert_allowed(context, action, str(target), allow_interrupt=allow_interrupt)
        if inside:
            relative = target.relative_to(base).parts
            if len(relative) >= 3 and relative[:2] == (".k41-agent", "outputs"):
                from agent.modules.tools.coding.storage import digest
                from agent.shared.thread_ids import storage_thread_id
                if relative[2] != digest(storage_thread_id(context.thread_id))[:24]:
                    raise CodingError("not_found", "Output file does not exist in this workspace/thread.")
        return target
