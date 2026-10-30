"""Portable validation for explicitly granted project environment values."""

import re

from agent.modules.tools.runtime.sandbox import _COMMON_ESSENTIAL, _UNIX_ESSENTIAL, _WINDOWS_ESSENTIAL

_RESERVED = _COMMON_ESSENTIAL | _UNIX_ESSENTIAL | _WINDOWS_ESSENTIAL | {
    "PYTHONIOENCODING", "PYTHONUTF8", "PYTHONDONTWRITEBYTECODE",
}


def validate_secret_name(name: str) -> str:
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,254}", name):
        raise ValueError("Secret names must be uppercase environment variable names of at most 255 characters.")
    if name in _RESERVED or name.startswith("K41_"):
        raise ValueError("This environment variable name is reserved for the execution runtime.")
    return name


def validate_secret_value(value: str) -> str:
    if not value or "\x00" in value or len(value.encode("utf-8")) > 16384:
        raise ValueError("Secret values must contain 1 to 16384 UTF-8 bytes without null characters.")
    return value


def validate_execution_secrets(values: dict[str, str], skill_environment=()) -> None:
    skill_names = {name.upper() for name, _ in skill_environment}
    for name, value in values.items():
        validate_secret_name(name)
        validate_secret_value(value)
        if name in skill_names:
            raise ValueError("A project secret conflicts with an active skill environment variable.")
