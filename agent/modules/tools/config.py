"""Configuration resolution for configurable tools."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import BaseTool

from agent.modules.tools.domain import (
    ToolConfigField,
    ToolConfigValue,
    ToolDescriptor,
)
from agent.shared.config import get_config_service

logger = logging.getLogger(__name__)


def _tool_config_key(tool_name: str, field_name: str) -> str:
    return f"tools.{tool_name}.{field_name}"


def _coerce_value(
    field: ToolConfigField,
    value: Any,
) -> ToolConfigValue:
    if value is None:
        return None
    if field.input_type == "boolean":
        from agent.shared.infrastructure.config_file import coerce_bool

        return coerce_bool(value)
    if field.input_type == "number":
        if value == "":
            return None
        number = float(value)
        if field.min is not None and number < field.min:
            raise ValueError(f"Tool config '{field.name}' is below minimum {field.min}.")
        if field.max is not None and number > field.max:
            raise ValueError(f"Tool config '{field.name}' is above maximum {field.max}.")
        return int(number) if number.is_integer() else number
    text = str(value)
    if field.input_type == "select" and field.options and text and text not in field.options:
        allowed = ", ".join(field.options)
        raise ValueError(
            f"Tool config '{field.name}' must be one of: {allowed}."
        )
    return text


class ToolConfigService:
    """Resolve effective per-tool config for an agent."""

    def resolve(
        self,
        descriptor: ToolDescriptor,
        agent_tool_configs: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, ToolConfigValue]:
        schema = descriptor.config_schema
        if schema is None:
            return {}

        field_map = schema.field_map()
        values: dict[str, Any] = {
            **schema.defaults(),
            **descriptor.default_config,
        }

        config_service = get_config_service()
        for field_name in field_map:
            setting = config_service.get_effective(
                _tool_config_key(descriptor.name, field_name)
            )
            if setting is not None:
                values[field_name] = setting.value

        overrides = (agent_tool_configs or {}).get(descriptor.name)
        if isinstance(overrides, dict):
            for field_name, value in overrides.items():
                if field_name in field_map:
                    values[field_name] = value

        coerced: dict[str, ToolConfigValue] = {}
        for field_name, field in field_map.items():
            value = _coerce_value(field, values.get(field_name))
            if field.required and value in (None, ""):
                raise ValueError(
                    f"Missing required config '{field_name}' for tool '{descriptor.name}'."
                )
            if value is not None:
                coerced[field_name] = value
        return coerced

    def materialize(
        self,
        descriptor: ToolDescriptor,
        agent_tool_configs: dict[str, dict[str, Any]] | None = None,
    ) -> BaseTool:
        if descriptor.factory is None:
            return descriptor.tool
        config = self.resolve(descriptor, agent_tool_configs)
        return descriptor.factory(config)


def materialize_tool(
    descriptor: ToolDescriptor,
    agent_tool_configs: dict[str, dict[str, Any]] | None = None,
) -> BaseTool:
    return ToolConfigService().materialize(descriptor, agent_tool_configs)


def serialize_tool_config_schemas(
    descriptors: list[ToolDescriptor],
) -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for descriptor in descriptors:
        if descriptor.config_schema is None:
            continue
        schemas[descriptor.name] = {
            **descriptor.config_schema.to_dict(),
            "default_config": {
                **descriptor.config_schema.defaults(),
                **descriptor.default_config,
            },
        }
    return schemas


__all__ = [
    "ToolConfigService",
    "materialize_tool",
    "serialize_tool_config_schemas",
]
