"""Permission modes and their run/card/global precedence."""

from enum import StrEnum
from typing import Any


class PermissionMode(StrEnum):
    ASK = "ask"
    AUTO = "auto"
    FULL_ACCESS = "full_access"


def resolve_mode(override: str | None, agent: Any, config: Any) -> PermissionMode:
    value = override or getattr(agent, "permission_mode", None)
    if value is None:
        value = config.get("tools.permission_mode", "auto")
    return PermissionMode(value or "auto")
