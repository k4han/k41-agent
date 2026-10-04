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
        return target
