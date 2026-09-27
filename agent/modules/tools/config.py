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
from agent.shared.config import ConfigService, get_config_service

logger = logging.getLogger(__name__)

# Placeholder returned instead of a raw credential in display-only payloads.
MASKED_SECRET_VALUE = "set"


def _tool_config_key(tool_name: str, field_name: str) -> str:
    return f"tools.{tool_name}.{field_name}"


def _is_secret_field(field: ToolConfigField) -> bool:
    return bool(field.secret) or field.input_type == "password"


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
        if value == "" or (isinstance(value, str) and value.strip() == ""):
            return None
        number = float(value)
        if field.min is not None and number < field.min:
            raise ValueError(f"Tool config '{field.name}' is below minimum {field.min}.")
        if field.max is not None and number > field.max:
            raise ValueError(f"Tool config '{field.name}' is above maximum {field.max}.")
        return int(number) if number.is_integer() else number
    text = str(value).strip()
    if field.input_type == "select" and field.options and text:
        if text in field.options:
            return text
        lowered = text.lower()
        for option in field.options:
            if option.lower() == lowered:
                return option
        allowed = ", ".join(field.options)
        raise ValueError(
            f"Tool config '{field.name}' must be one of: {allowed}."
        )
    return text


def coerce_tool_config_value(field: ToolConfigField, value: Any) -> ToolConfigValue:
    """Public wrapper around field-level coercion for dashboard write validation."""
    return _coerce_value(field, value)


def normalize_tool_setting_value(key: str, value: Any) -> Any:
    """Validate and normalize a global ``tools.<tool>.<field>`` setting value.

    Raises:
        ValueError: When the tool or field is unknown, or the value is invalid
            for the field's schema (bad select option, out-of-range number, ...).

    Empty strings are normalized to ``None`` so callers can treat them as a
    reset to the schema default (delete the override).
    """
    parts = key.split(".", 2)
    if len(parts) != 3 or parts[0] != "tools":
        return value
    _, tool_name, field_name = parts

    from agent.modules.tools import ToolSource, find_descriptors

    descriptor = next(
        (item for item in find_descriptors(source=ToolSource.BUILTIN) if item.name == tool_name),
        None,
    )
    if descriptor is None:
        raise ValueError(f"Unknown tool '{tool_name}'.")
    if descriptor.config_schema is None:
        return value
    field = descriptor.config_schema.field_map().get(field_name)
    if field is None:
        allowed = ", ".join(sorted(descriptor.config_schema.field_map()))
        raise ValueError(
            f"Unknown config field '{field_name}' for tool '{tool_name}'. "
            f"Allowed fields: {allowed}."
        )
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == MASKED_SECRET_VALUE and _is_secret_field(field):
            raise ValueError(
                f"Tool config '{field_name}' uses placeholder '{MASKED_SECRET_VALUE}' "
                "which is display-only. Send a real value, empty string, or null "
                "to keep/reset the stored credential."
            )
    coerced = _coerce_value(field, value)
    if coerced == "":
        return None
    return coerced


def seed_tool_runtime_defaults(service: ConfigService | None = None) -> int:
    """Register ``tools.*`` runtime keys and defaults from builtin tool schemas.

    Makes configurable tool keys visible in settings listings even before they
    are explicitly set. Safe to call repeatedly (idempotent).
    """
    try:
        from agent.modules.tools import ToolSource, find_descriptors
        from agent.shared.config.service import register_runtime_defaults

        schemas = serialize_tool_config_schemas(
            find_descriptors(source=ToolSource.BUILTIN)
        )
        defaults: dict[str, Any] = {}
        for tool_name, schema in schemas.items():
            default_config = schema.get("default_config") or {}
            for field in schema.get("fields") or []:
                field_name = field.get("name")
                if not isinstance(field_name, str) or not field_name:
                    continue
                value = default_config.get(field_name)
                if value is None:
                    value = field.get("default")
                if value is None:
                    value = ""
                defaults[f"tools.{tool_name}.{field_name}"] = value
        if not defaults:
            return 0
        register_runtime_defaults(defaults, service)
        return len(defaults)
    except Exception:
        logger.exception("Failed to seed tool runtime defaults")
        return 0


def resolve_global_tool_config_schemas(
    service: ConfigService | None = None,
) -> dict[str, dict[str, ToolConfigValue]]:
    """Resolve global effective config for every configurable builtin tool.

    Mirrors :meth:`ToolConfigService.resolve` without agent-level overrides,
    and without raising on missing required fields (used for display only).

    Secret fields are masked so this payload can never leak raw credentials;
    callers that need the real value (dashboard edit forms) read it from the
    settings listing instead.
    """
    from agent.modules.tools import ToolSource, find_descriptors

    config_service = service or get_config_service()
    result: dict[str, dict[str, ToolConfigValue]] = {}
    for descriptor in find_descriptors(source=ToolSource.BUILTIN):
        schema = descriptor.config_schema
        if schema is None:
            continue
        values: dict[str, ToolConfigValue] = {
            **schema.defaults(),
            **descriptor.default_config,
        }
        for field_name, field in schema.field_map().items():
            value = config_service.get(_tool_config_key(descriptor.name, field_name))
            if value is None:
                continue
            if _is_secret_field(field) and str(value).strip():
                value = MASKED_SECRET_VALUE
            values[field_name] = value
        result[descriptor.name] = values
    return result


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
            # A stored null means "reset to default" — ignore it.
            if setting is not None and setting.value is not None:
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
        # local import to avoid circular dependency at module-import time
        from agent.modules.tools.middleware import apply_default_middleware

        # Factory-built instances are new objects, so they miss the default
        # middleware chain that was applied to the registered default tool.
        return apply_default_middleware(descriptor.factory(config))


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
    "MASKED_SECRET_VALUE",
    "ToolConfigService",
    "coerce_tool_config_value",
    "materialize_tool",
    "normalize_tool_setting_value",
    "resolve_global_tool_config_schemas",
    "seed_tool_runtime_defaults",
    "serialize_tool_config_schemas",
]
