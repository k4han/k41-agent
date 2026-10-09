"""Filesystem path confinement shared by local and sandbox execution."""

from __future__ import annotations

import os
from pathlib import Path

from agent.modules.tools.coding.contracts import CodingError, InvocationContext


def comparable_path(value) -> Path:
    path = Path(value).resolve()
    raw = str(path)
    if os.name == "nt":
        if raw.startswith("\\\\?\\UNC\\"):
            raw = "\\\\" + raw[8:]
        elif raw.startswith("\\\\?\\"):
            raw = raw[4:]
    return Path(raw)


class PathPermissions:
    def resolve_path(self, context: InvocationContext, value: str, action: str,
                     *, authorize: bool = True, allow_interrupt: bool = True) -> Path:
        base = comparable_path(context.workspace)
        requested = Path(value or ".").expanduser()
        target = comparable_path(requested if requested.is_absolute() else base / requested)
        active_skill = any(target.is_relative_to(comparable_path(root)) for root in context.skill_roots)
        shared_cache = base / ".k41-agent" / "s"
        if target.is_relative_to(shared_cache) or (context.skill_cache_root and target.is_relative_to(comparable_path(context.skill_cache_root))):
            if not active_skill:
                raise CodingError("not_found", "Skill resource is not active in this agent/workspace/thread.")
        if active_skill and action not in {"read", "shell"}:
            raise CodingError("permission_denied", "Activated skill resources are read-only. Write task output in the workspace.")
        try:
            inside = os.path.commonpath([os.path.normcase(base), os.path.normcase(target)]) == os.path.normcase(base)
        except ValueError:
            inside = False
        if not inside and not active_skill:
            if not requested.is_absolute():
                raise CodingError("permission_denied", "Relative paths and symlinks may not escape the workspace.")
            if authorize:
                self.assert_allowed(context, "external_directory", str(target.parent if action != "shell" else target), allow_interrupt=allow_interrupt)
        if authorize:
            self.assert_allowed(context, action, str(target), allow_interrupt=allow_interrupt)
        if os.name == "nt" and len(str(target)) >= 240:
            raw = str(target)
            return Path("\\\\?\\UNC\\" + raw[2:] if raw.startswith("\\\\") else "\\\\?\\" + raw)
        return target

    def is_skill_path(self, context: InvocationContext, value) -> bool:
        target = comparable_path(value)
        return any(target.is_relative_to(comparable_path(root)) for root in context.skill_roots)
