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
    }


class PromptVariableService:
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
        return normalized

    async def list_variables(self) -> list[dict[str, Any]]:
        records = await self._repository.list()
        return [serialize_prompt_variable(record) for record in records]

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
        normalized_current_name = self.validate_name(current_name)
        normalized_name = self.validate_name(name)
        record = await self._repository.update(
            current_name=normalized_current_name,
            name=normalized_name,
            value=str(value or ""),
        )
        return serialize_prompt_variable(record)

    async def delete_variable(self, name: str) -> None:
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
