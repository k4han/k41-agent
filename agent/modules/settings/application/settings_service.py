"""Core runtime settings service — merges values from multiple sources by precedence.

Precedence (low → high):
    DEFAULT → CONFIG_FILE → DATABASE → ENV_OVERRIDE
"""

from __future__ import annotations

from agent.modules.settings.domain.ports import SettingsRepository
from agent.modules.settings.domain.settings_value import (
    KNOWN_RUNTIME_KEYS,
    RuntimeSettings,
    SettingsValue,
)
from agent.shared.infrastructure.config_file import coerce_bool


class RuntimeSettingsService:
    """Merge settings from an ordered list of repositories.

    Repositories are provided in ascending-precedence order: the *last*
    repository that supplies a key wins.
    """

    def __init__(self, repositories: list[SettingsRepository]) -> None:
        self._repos = list(repositories)

    # --- public API ---------------------------------------------------

    def get_effective(self, key: str) -> SettingsValue | None:
        """Return the effective value for *key* after precedence merge."""
        result: SettingsValue | None = None
        for repo in self._repos:
            val = repo.get(key)
            if val is not None:
                result = val
        return result

    def list_all(self) -> dict[str, SettingsValue]:
        """Return all effective settings keyed by canonical name."""
        merged: dict[str, SettingsValue] = {}
        for repo in self._repos:
            repo_all = repo.get_all()
            for key in KNOWN_RUNTIME_KEYS:
                if key in repo_all:
                    merged[key] = repo_all[key]
        return merged

    def list_all_by_source(self) -> dict[str, list[SettingsValue]]:
        """Return every value from every source, grouped by key.

        Useful for the dashboard to show where each value comes from.
        """
        by_key: dict[str, list[SettingsValue]] = {}
        for repo in self._repos:
            for key, val in repo.get_all().items():
                if key in KNOWN_RUNTIME_KEYS:
                    by_key.setdefault(key, []).append(val)
        return by_key

    def get_runtime_settings(self) -> RuntimeSettings:
        """Build a consolidated ``RuntimeSettings`` from merged sources."""
        merged = self.list_all()
        defaults = RuntimeSettings()
        channel_enabled = dict(defaults.channel_enabled)
        for channel_name in channel_enabled:
            canon = f"channels.{channel_name}.enabled"
            sv = merged.get(canon)
            if sv is not None:
                channel_enabled[channel_name] = coerce_bool(sv.value)
        return RuntimeSettings(channel_enabled=channel_enabled)

    def get_settings_overview(self) -> dict[str, dict[str, object]]:
        """Dashboard-friendly overview: effective value + source for each key."""
        merged = self.list_all()
        return {
            key: {
                "value": sv.value,
                "source": sv.source.value,
            }
            for key, sv in sorted(merged.items())
        }

    def get_settings_sources(self) -> dict[str, list[dict[str, object]]]:
        """Dashboard-friendly: all sources for each key."""
        by_key = self.list_all_by_source()
        return {
            key: [
                {"value": sv.value, "source": sv.source.value}
                for sv in vals
            ]
            for key, vals in sorted(by_key.items())
        }


__all__ = ["RuntimeSettingsService"]
