from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agent.modules.channels.contracts import (
    ChannelSettingField,
    ChatChannelAdapter,
)
from agent.modules.channels.service_specs import (
    BUILTIN_CHANNEL_DESCRIPTORS,
    ChannelDescriptor,
)
from agent.shared.integrations import (
    IntegrationAvailability,
    LazyIntegrationRegistry,
)


class ChannelRegistry:
    def __init__(self) -> None:
        self._lazy = LazyIntegrationRegistry("channel")
        self._adapters: dict[str, ChatChannelAdapter] = {}

    def register_descriptor(
        self,
        descriptor: ChannelDescriptor,
        *,
        replace: bool = False,
    ) -> None:
        self._lazy.register(descriptor, replace=replace)

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

    def load_adapter(self, name: str) -> ChatChannelAdapter:
        normalized = name.strip().lower()
        adapter = self._adapters.get(normalized)
        if adapter is not None:
            return adapter
        adapter = self._lazy.load(normalized)
        self.register(adapter, replace=True)
        return adapter

    def unregister(self, name: str) -> None:
        self._adapters.pop(name.strip().lower(), None)

    def require(self, name: str) -> ChatChannelAdapter:
        return self.load_adapter(name)

    def list(self) -> list[ChatChannelAdapter]:
        return list(self._adapters.values())

    def names(self) -> list[str]:
        names = set(self._lazy.names())
        names.update(self._adapters.keys())
        return sorted(names)

    def descriptors(self) -> list[ChannelDescriptor]:
        return [
            descriptor
            for descriptor in self._lazy.list_descriptors()
            if isinstance(descriptor, ChannelDescriptor)
        ]

    def descriptor(self, name: str) -> ChannelDescriptor | None:
        descriptor = self._lazy.get_descriptor(name)
        return descriptor if isinstance(descriptor, ChannelDescriptor) else None

    def availability(self, name: str) -> IntegrationAvailability:
        if name.strip().lower() in self._adapters:
            return IntegrationAvailability(available=True)
        return self._lazy.availability(name)

    def ensure_available(self, name: str) -> IntegrationAvailability:
        if name.strip().lower() in self._adapters:
            return IntegrationAvailability(available=True)
        return self._lazy.ensure_available(name)

    def setting_field(self, key: str) -> ChannelSettingField | None:
        parsed = parse_channel_setting_key(key)
        if parsed is None:
            return None
        channel_name, field_name = parsed

        descriptor = self.descriptor(channel_name)
        if descriptor is not None:
            return next(
                (
                    field
                    for field in descriptor.settings_schema
                    if isinstance(field, ChannelSettingField)
                    and field.name == field_name
                ),
                None,
            )

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
_builtins_registered = False


def get_channel_registry() -> ChannelRegistry:
    ensure_builtin_channel_descriptors()
    return _registry


def ensure_builtin_channel_descriptors() -> None:
    global _builtins_registered
    if _builtins_registered:
        return
    for descriptor in BUILTIN_CHANNEL_DESCRIPTORS:
        _registry.register_descriptor(descriptor, replace=True)
    _builtins_registered = True


def register_channel_descriptors(
    descriptors: Iterable[ChannelDescriptor],
    *,
    replace: bool = False,
) -> None:
    registry = get_channel_registry()
    for descriptor in descriptors:
        registry.register_descriptor(descriptor, replace=replace)


def register_channel_adapters(
    adapters: Iterable[ChatChannelAdapter],
    *,
    replace: bool = False,
) -> None:
    registry = get_channel_registry()
    for adapter in adapters:
        registry.register(adapter, replace=replace)


def get_channel_setting_field(key: str) -> ChannelSettingField | None:
    return get_channel_registry().setting_field(key)


def _serialize_settings_schema(channel_name: str, fields: tuple[Any, ...]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for field in fields:
        if not isinstance(field, ChannelSettingField):
            continue
        serialized.append(
            {
                "name": field.name,
                "key": field.config_key(channel_name),
                "label": field.label,
                "description": field.description,
                "input_type": field.input_type,
                "required": field.required,
                "secret": field.secret,
                "section": field.section,
                "default": field.default,
            }
        )
    return serialized


def _serialize_settings_sections(sections: tuple[Any, ...]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for section in sections:
        serialized.append(
            {
                "id": getattr(section, "id", ""),
                "title": getattr(section, "title", ""),
                "subtitle": getattr(section, "subtitle", ""),
                "default_collapsed": getattr(section, "default_collapsed", False),
            }
        )
    return serialized


def serialize_channel_descriptor(descriptor: ChannelDescriptor) -> dict[str, Any]:
    availability = get_channel_registry().availability(descriptor.name)
    return {
        "name": descriptor.name,
        "title": descriptor.title,
        "summary": descriptor.summary,
        "tagline": descriptor.tagline,
        "capabilities": sorted(descriptor.capabilities),
        "settings": _serialize_settings_schema(
            descriptor.name,
            descriptor.settings_schema,
        ),
        "sections": _serialize_settings_sections(descriptor.settings_sections),
        "required_env": [],
        "availability": availability.to_dict(),
        "install_extra": descriptor.install_extra,
    }


def serialize_channel_adapter(adapter: ChatChannelAdapter) -> dict[str, Any]:
    return {
        "name": adapter.name,
        "title": adapter.title,
        "summary": adapter.summary,
        "tagline": adapter.tagline,
        "capabilities": sorted(adapter.capabilities),
        "settings": _serialize_settings_schema(adapter.name, adapter.settings_schema),
        "sections": _serialize_settings_sections(adapter.settings_sections),
        "required_env": [],
        "availability": IntegrationAvailability(available=True).to_dict(),
        "install_extra": "",
    }


def list_channel_catalog() -> list[dict[str, Any]]:
    registry = get_channel_registry()
    catalog = [serialize_channel_descriptor(descriptor) for descriptor in registry.descriptors()]
    descriptor_names = {item["name"] for item in catalog}
    catalog.extend(
        serialize_channel_adapter(adapter)
        for adapter in registry.list()
        if adapter.name not in descriptor_names
    )
    return sorted(catalog, key=lambda item: str(item["name"]))


__all__ = [
    "ChannelRegistry",
    "ensure_builtin_channel_descriptors",
    "get_channel_registry",
    "get_channel_setting_field",
    "list_channel_catalog",
    "parse_channel_setting_key",
    "register_channel_adapters",
    "register_channel_descriptors",
    "serialize_channel_adapter",
    "serialize_channel_descriptor",
]
