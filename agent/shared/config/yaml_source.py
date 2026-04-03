from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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
                logger.warning("Config file %s does not contain a mapping — ignoring.", self._path)
                self._cache = {}
                return self._cache

            self._cache = self._flatten(data)
            return self._cache
        except FileNotFoundError:
            logger.debug("Config file not found: %s — using empty config.", self._path)
            self._cache = {}
            return self._cache
        except ImportError:
            logger.warning("PyYAML is not installed — skipping config file %s.", self._path)
            self._cache = {}
            return self._cache
        except Exception:
            logger.exception("Failed to read config file %s.", self._path)
            self._cache = {}
            return self._cache

    def _flatten(self, data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
        """Flatten nested dict to dot-notation keys.

        Example:
            {"llm": {"api_key": "sk-123"}} -> {"llm.api_key": "sk-123"}
        """
        result: dict[str, Any] = {}
        for key, value in data.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                result.update(self._flatten(value, full_key))
            else:
                result[full_key] = value
        return result


__all__ = ["DEFAULT_CONFIG_PATH", "YamlConfigSource"]
