"""Pure factories for ConfigService without module-level singletons.

This module owns explicit construction of ConfigService instances.
Callers must pass the resulting instance through AppContainer instead
of relying on the legacy global in service.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def create_config_service(
    yaml_path: Path | None = None,
    extra_sources: list[Any] | None = None,
) -> Any:
    """Build a new ConfigService with default + YAML sources."""
    from agent.shared.config.default_source import DefaultConfigSource
    from agent.shared.config.service import ConfigService
    from agent.shared.config.yaml_source import YamlConfigSource

    sources: list[Any] = [DefaultConfigSource(), YamlConfigSource(path=yaml_path)]
    if extra_sources:
        sources.extend(extra_sources)
    return ConfigService(sources)


__all__ = ["create_config_service"]
