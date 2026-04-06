from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path.home() / ".kaka-agent" / "config.yaml"


def flatten_config_mapping(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten a nested mapping into dot-separated keys."""
    items: dict[str, Any] = {}
    for key, value in data.items():
        full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(value, dict):
            items.update(flatten_config_mapping(value, full_key))
        else:
            items[full_key] = value
    return items


def unflatten_config_mapping(flat_data: dict[str, Any]) -> dict[str, Any]:
    """Unflatten dot-separated keys into nested mapping."""
    result: dict[str, Any] = {}
    for key, value in flat_data.items():
        keys = key.split(".")
        current = result
        for k in keys[:-1]:
            if k not in current or not isinstance(current[k], dict):
                current[k] = {}
            current = current[k]
        current[keys[-1]] = value
    return result


def merge_nested_dicts(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Recursively merge source dict into target dict."""
    for k, v in source.items():
        if k in target and isinstance(target[k], dict) and isinstance(v, dict):
            merge_nested_dicts(target[k], v)
        else:
            target[k] = v


def coerce_bool(value: object) -> bool:
    """Coerce a value to boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def load_flat_config_file(path: Path | None = None) -> dict[str, Any]:
    """Load a YAML config file and return a flattened mapping."""
    config_path = path or DEFAULT_CONFIG_PATH

    try:
        import yaml

        raw = config_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            logger.warning("Config file %s does not contain a mapping — ignoring.", config_path)
            return {}
        return flatten_config_mapping(data)
    except FileNotFoundError:
        logger.debug("Config file not found: %s — using empty config.", config_path)
        return {}
    except ImportError:
        logger.warning("PyYAML is not installed — skipping config file %s.", config_path)
        return {}
    except Exception:
        logger.exception("Failed to read config file %s.", config_path)
        return {}


__all__ = ["DEFAULT_CONFIG_PATH", "coerce_bool", "flatten_config_mapping", "unflatten_config_mapping", "merge_nested_dicts", "load_flat_config_file"]
