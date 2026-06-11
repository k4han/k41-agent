from importlib import import_module
from typing import Any

from agent.bootstrap.runtime import (
    AppRuntime,
    BUILTIN_CHANNEL_DESCRIPTORS,
    ChannelDescriptor,
)
from agent.bootstrap.settings import (
    BootstrapConfig,
    load_bootstrap_config,
)

_APP_EXPORTS = {"app", "create_app", "main", "run", "settings"}


def __getattr__(name: str) -> Any:
    if name in _APP_EXPORTS:
        app_module = import_module("agent.bootstrap.app")
        return getattr(app_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "app",
    "create_app",
    "main",
    "run",
    "settings",
    "AppRuntime",
    "BUILTIN_CHANNEL_DESCRIPTORS",
    "ChannelDescriptor",
    "BootstrapConfig",
    "load_bootstrap_config",
]
