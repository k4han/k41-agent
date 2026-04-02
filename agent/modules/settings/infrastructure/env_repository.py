"""Environment-variable based settings repository.

Maps runtime env vars to canonical settings keys.
"""

from __future__ import annotations

import os

from agent.modules.settings.domain.settings_value import SettingsSource, SettingsValue
from agent.shared.infrastructure.config_file import coerce_bool

# Mapping: env var name → (settings key, type-coercion callable)
_ENV_MAP: list[tuple[str, str, type]] = [
    ("ENABLE_TELEGRAM", "channels.telegram.enabled", bool),
    ("ENABLE_DISCORD", "channels.discord.enabled", bool),
]


def _coerce(raw: str, target_type: type) -> object:
    if target_type is bool:
        return coerce_bool(raw)
    if target_type is int:
        return int(raw)
    return raw


class EnvSettingsRepository:
    """Read settings from environment variables."""

    def get_all(self) -> dict[str, SettingsValue]:
        result: dict[str, SettingsValue] = {}
        for env_var, key, typ in _ENV_MAP:
            raw = os.getenv(env_var)
            if raw is not None:
                result[key] = SettingsValue(
                    key=key,
                    value=_coerce(raw, typ),
                    source=SettingsSource.ENV_OVERRIDE,
                )
        return result

    def get(self, key: str) -> SettingsValue | None:
        for env_var, k, typ in _ENV_MAP:
            if k == key:
                raw = os.getenv(env_var)
                if raw is not None:
                    return SettingsValue(
                        key=key,
                        value=_coerce(raw, typ),
                        source=SettingsSource.ENV_OVERRIDE,
                    )
                return None
        return None


__all__ = ["EnvSettingsRepository"]
