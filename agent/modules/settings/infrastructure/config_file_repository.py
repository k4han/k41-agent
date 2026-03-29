"""Config-file based settings repository.

Reads settings from ``~/.kaka-agent/config.yml`` (YAML).
If the file does not exist the repository gracefully returns an empty dict.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agent.modules.settings.domain.settings_value import SettingsSource, SettingsValue

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path.home() / ".kaka-agent" / "config.yml"


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten a nested dict into dot-separated keys.

    Example::

        {"channels": {"telegram": {"enabled": True}}}
        → {"channels.telegram.enabled": True}
    """
    items: dict[str, Any] = {}
    for key, value in data.items():
        full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(value, dict):
            items.update(_flatten(value, full_key))
        else:
            items[full_key] = value
    return items


class ConfigFileRepository:
    """Read settings from a YAML config file."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or DEFAULT_CONFIG_PATH
        self._cache: dict[str, SettingsValue] | None = None

    def _load(self) -> dict[str, SettingsValue]:
        if self._cache is not None:
            return self._cache

        if not self._path.exists():
            logger.debug("Config file not found: %s — using empty config.", self._path)
            self._cache = {}
            return self._cache

        try:
            import yaml  # lazy import — optional dependency

            raw = self._path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
            if not isinstance(data, dict):
                logger.warning("Config file %s does not contain a mapping — ignoring.", self._path)
                self._cache = {}
                return self._cache

            flat = _flatten(data)
            self._cache = {
                key: SettingsValue(key=key, value=val, source=SettingsSource.CONFIG_FILE)
                for key, val in flat.items()
            }
        except ImportError:
            logger.warning("PyYAML is not installed — skipping config file %s.", self._path)
            self._cache = {}
        except Exception:
            logger.exception("Failed to read config file %s.", self._path)
            self._cache = {}

        return self._cache

    def get_all(self) -> dict[str, SettingsValue]:
        return dict(self._load())

    def get(self, key: str) -> SettingsValue | None:
        return self._load().get(key)

    def reload(self) -> None:
        """Invalidate the cache so the next access re-reads the file."""
        self._cache = None


__all__ = ["ConfigFileRepository", "DEFAULT_CONFIG_PATH"]
