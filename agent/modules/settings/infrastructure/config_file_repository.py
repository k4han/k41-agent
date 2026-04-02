"""Config-file based settings repository.

Reads settings from ``~/.kaka-agent/config.yml`` (YAML).
If the file does not exist the repository gracefully returns an empty dict.
"""

from __future__ import annotations

import logging
from pathlib import Path

from agent.modules.settings.domain.settings_value import (
    SettingsSource,
    SettingsValue,
)
from agent.shared.infrastructure.config_file import (
    DEFAULT_CONFIG_PATH,
    load_flat_config_file,
)

logger = logging.getLogger(__name__)


class ConfigFileRepository:
    """Read runtime settings from a YAML config file."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or DEFAULT_CONFIG_PATH
        self._cache: dict[str, SettingsValue] | None = None

    def _load(self) -> dict[str, SettingsValue]:
        if self._cache is not None:
            return self._cache

        flat = load_flat_config_file(self._path)
        self._cache = {
            key: SettingsValue(key=key, value=val, source=SettingsSource.CONFIG_FILE)
            for key, val in flat.items()
        }

        return self._cache

    def get_all(self) -> dict[str, SettingsValue]:
        return self._load()

    def get(self, key: str) -> SettingsValue | None:
        return self._load().get(key)

    def reload(self) -> None:
        """Invalidate the cache so the next access re-reads the file."""
        self._cache = None


__all__ = ["ConfigFileRepository", "DEFAULT_CONFIG_PATH"]
