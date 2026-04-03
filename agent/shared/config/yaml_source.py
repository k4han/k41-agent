from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agent.shared.config.models import (
    SettingsSource,
    SettingsValue,
    build_settings_values,
)
from agent.shared.infrastructure.config_file import flatten_config_mapping

logger = logging.getLogger(__name__)

_MISSING = object()

DEFAULT_CONFIG_PATH = Path.home() / ".kaka-agent" / "config.yaml"


class YamlConfigSource:
    """Read configuration from YAML file.

    This source loads configuration from a YAML file and flattens nested
    structures into dot-notation keys (e.g., llm.api_key).
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or DEFAULT_CONFIG_PATH
        self._cache: dict[str, Any] | None = None
        self._priority = 100  # Middle priority

    def get(self, key: str) -> Any | None:
        """Get a config value from YAML file."""
        data = self._load()
        return data.get(key)

    def get_all(self) -> dict[str, Any]:
        """Get all config values from YAML file."""
        return self._load()

    def get_settings_value(self, key: str) -> SettingsValue | None:
        """Get a config value as SettingsValue."""
        data = self._load()
        val = data.get(key, _MISSING)
        if val is _MISSING:
            return None
        return SettingsValue(key=key, value=val, source=SettingsSource.CONFIG_FILE)

    def get_all_settings_values(
        self, keys: set[str] | None = None
    ) -> dict[str, SettingsValue]:
        """Get all config values as SettingsValue objects.

        Args:
            keys: Optional set of keys to filter. If None, returns all keys.
        """
        data = self._load()
        return build_settings_values(data, SettingsSource.CONFIG_FILE, keys)

    def reload(self) -> None:
        """Clear cache and reload from file."""
        self._cache = None

    @property
    def priority(self) -> int:
        """Return priority (100 = middle)."""
        return self._priority

    def _load(self) -> dict[str, Any]:
        """Load and cache config from YAML file."""
        if self._cache is not None:
            return self._cache

        try:
            import yaml

            raw = self._path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
            if not isinstance(data, dict):
                logger.warning(
                    "Config file %s does not contain a mapping — ignoring.", self._path
                )
                self._cache = {}
                return self._cache

            self._cache = flatten_config_mapping(data)
            return self._cache
        except FileNotFoundError:
            logger.debug("Config file not found: %s — using empty config.", self._path)
            self._cache = {}
            return self._cache
        except ImportError:
            logger.warning(
                "PyYAML is not installed — skipping config file %s.", self._path
            )
            self._cache = {}
            return self._cache
        except Exception:
            logger.exception("Failed to read config file %s.", self._path)
            self._cache = {}
            return self._cache


__all__ = ["DEFAULT_CONFIG_PATH", "YamlConfigSource"]
