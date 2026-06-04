from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agent.modules.channels.contracts import (
    ChannelSettingField,
    ChatChannelAdapter,
)


class ChannelRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ChatChannelAdapter] = {}

    def register(self, adapter: ChatChannelAdapter, *, replace: bool = False) -> None:
        name = adapter.name.strip().lower()
        if not name:
            raise ValueError("Channel adapter name is required.")
        if name in self._adapters and not replace:
            if self._adapters[name] is adapter:
                return
            raise ValueError(f"Channel adapter '{name}' is already registered.")
        self._adapters[name] = adapter

    def get(self, name: str) -> ChatChannelAdapter | None:
        return self._adapters.get(name.strip().lower())

    def unregister(self, name: str) -> None:
        self._adapters.pop(name.strip().lower(), None)

    def require(self, name: str) -> ChatChannelAdapter:
        adapter = self.get(name)
        if adapter is None:
            raise KeyError(f"Channel adapter '{name}' is not registered.")
        return adapter

    def list(self) -> list[ChatChannelAdapter]:
        return list(self._adapters.values())

    def names(self) -> list[str]:
        return list(self._adapters.keys())

    def setting_field(self, key: str) -> ChannelSettingField | None:
        parsed = parse_channel_setting_key(key)
        if parsed is None:
            return None
        channel_name, field_name = parsed
        adapter = self.get(channel_name)
        if adapter is None:
            return None
        return next(
            (field for field in adapter.settings_schema if field.name == field_name),
            None,
        )


def parse_channel_setting_key(key: str) -> tuple[str, str] | None:
    parts = key.split(".")
    if len(parts) != 3 or parts[0] != "channels":
        return None
    channel_name = parts[1].strip().lower()
    field_name = parts[2].strip()
    if not channel_name or not field_name:
        return None
    return channel_name, field_name


_registry = ChannelRegistry()


def get_channel_registry() -> ChannelRegistry:
    return _registry


def register_channel_adapters(
    adapters: Iterable[ChatChannelAdapter],
    *,
    replace: bool = False,
) -> None:
    for adapter in adapters:
        _registry.register(adapter, replace=replace)


def get_channel_setting_field(key: str) -> ChannelSettingField | None:
    return _registry.setting_field(key)


def serialize_channel_adapter(adapter: ChatChannelAdapter) -> dict[str, Any]:
    sections = [
        {
            "id": section.id,
            "title": section.title,
            "subtitle": section.subtitle,
            "default_collapsed": section.default_collapsed,
        }
        for section in adapter.settings_sections
    ]
    fields = [
        {
            "name": field.name,
            "key": field.config_key(adapter.name),
            "label": field.label,
            "description": field.description,
            "input_type": field.input_type,
            "required": field.required,
            "secret": field.secret,
            "section": field.section,
            "default": field.default,
        }
        for field in adapter.settings_schema
    ]
    return {
        "name": adapter.name,
        "title": adapter.title,
        "summary": adapter.summary,
        "tagline": adapter.tagline,
        "capabilities": sorted(adapter.capabilities),
        "settings": fields,
        "sections": sections,
        "required_env": [],
    }


def list_channel_catalog() -> list[dict[str, Any]]:
    return [serialize_channel_adapter(adapter) for adapter in _registry.list()]


__all__ = [
    "ChannelRegistry",
    "get_channel_registry",
    "get_channel_setting_field",
    "list_channel_catalog",
    "parse_channel_setting_key",
    "register_channel_adapters",
    "serialize_channel_adapter",
]
