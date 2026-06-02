from __future__ import annotations

import logging
import re
from typing import Any

from agent.modules.prompt_variables.models import PromptVariable
from agent.modules.prompt_variables.repository import PromptVariableRepository

logger = logging.getLogger(__name__)

PROMPT_VARIABLE_NAME_PATTERN = r"[A-Za-z][A-Za-z0-9_-]{0,63}"
_PROMPT_VARIABLE_NAME_RE = re.compile(rf"^{PROMPT_VARIABLE_NAME_PATTERN}$")


def serialize_prompt_variable(record: PromptVariable) -> dict[str, Any]:
    return {
        "name": record.name,
        "value": record.value or "",
        "placeholder": f"{{{{{record.name}}}}}",
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "is_system": False,
    }


class PromptVariableService:
    SYSTEM_VARIABLE_NAMES = {
        "current_time",
        "operating_system",
        "workspace",
        "working_dir",
        "user_name",
    }

    def __init__(self, repository: PromptVariableRepository | None = None) -> None:
        self._repository = repository or PromptVariableRepository()

    @staticmethod
    def validate_name(name: str) -> str:
        normalized = str(name or "").strip()
        if not normalized:
            raise ValueError("Prompt variable name is required.")
        if not _PROMPT_VARIABLE_NAME_RE.fullmatch(normalized):
            raise ValueError(
                "Prompt variable name must start with a letter and contain only "
                "letters, numbers, underscores, or hyphens."
            )
        if normalized in PromptVariableService.SYSTEM_VARIABLE_NAMES:
            raise ValueError(f"'{normalized}' is a reserved system prompt variable.")
        return normalized

    async def list_variables(self) -> list[dict[str, Any]]:
        import sys
        import getpass

        # Get system info
        os_name = sys.platform
        if os_name == "win32":
            os_name = "windows"
        elif os_name == "darwin":
            os_name = "macos"

        try:
            username = getpass.getuser()
        except Exception:
            username = "user"

        system_vars = [
            {
                "name": "current_time",
                "value": "(Dynamic datetime resolved at prompt evaluation)",
                "placeholder": "{{current_time}}",
                "is_system": True,
                "created_at": None,
                "updated_at": None,
            },
            {
                "name": "operating_system",
                "value": os_name,
                "placeholder": "{{operating_system}}",
                "is_system": True,
                "created_at": None,
                "updated_at": None,
            },
            {
                "name": "workspace",
                "value": "(Friendly workspace label, e.g. owner/repo for GitHub-backed sandboxes)",
                "placeholder": "{{workspace}}",
                "is_system": True,
                "created_at": None,
                "updated_at": None,
            },
            {
                "name": "working_dir",
                "value": "(Actual on-disk cwd the agent runs in, resolved at runtime)",
                "placeholder": "{{working_dir}}",
                "is_system": True,
                "created_at": None,
                "updated_at": None,
            },
            {
                "name": "user_name",
                "value": username,
                "placeholder": "{{user_name}}",
                "is_system": True,
                "created_at": None,
                "updated_at": None,
            },
        ]

        records = await self._repository.list()
        user_vars = [serialize_prompt_variable(record) for record in records]
        return system_vars + user_vars

    async def value_map(self) -> dict[str, str]:
        records = await self._repository.list()
        return {record.name: record.value or "" for record in records}

    async def create_variable(self, *, name: str, value: str) -> dict[str, Any]:
        normalized_name = self.validate_name(name)
        record = await self._repository.create(
            name=normalized_name,
            value=str(value or ""),
        )
        return serialize_prompt_variable(record)

    async def update_variable(
        self,
        *,
        current_name: str,
        name: str,
        value: str,
    ) -> dict[str, Any]:
        # Validate current name to prevent modifying system variable if somehow hit
        if current_name in PromptVariableService.SYSTEM_VARIABLE_NAMES:
            raise ValueError(f"'{current_name}' is a reserved system prompt variable.")
        
        normalized_current_name = self.validate_name(current_name)
        normalized_name = self.validate_name(name)
        record = await self._repository.update(
            current_name=normalized_current_name,
            name=normalized_name,
            value=str(value or ""),
        )
        return serialize_prompt_variable(record)

    async def delete_variable(self, name: str) -> None:
        if name in PromptVariableService.SYSTEM_VARIABLE_NAMES:
            raise ValueError(f"'{name}' is a reserved system prompt variable.")
        
        normalized_name = self.validate_name(name)
        deleted = await self._repository.delete(normalized_name)
        if not deleted:
            raise FileNotFoundError(f"Prompt variable '{normalized_name}' does not exist.")


_service: PromptVariableService | None = None


def get_prompt_variable_service() -> PromptVariableService:
    global _service
    if _service is None:
        _service = PromptVariableService()
    return _service


async def get_prompt_variable_values() -> dict[str, str]:
    return await get_prompt_variable_service().value_map()


async def get_runtime_prompt_variable_values() -> dict[str, str]:
    try:
        return await get_prompt_variable_values()
    except RuntimeError as exc:
        if "Async engine not initialized" in str(exc):
            logger.debug("Prompt variables skipped because persistence is not initialized.")
            return {}
        raise


__all__ = [
    "PROMPT_VARIABLE_NAME_PATTERN",
    "PromptVariableService",
    "get_prompt_variable_service",
    "get_prompt_variable_values",
    "get_runtime_prompt_variable_values",
    "serialize_prompt_variable",
]
