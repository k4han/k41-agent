"""Core settings service — merges values from multiple sources by precedence.

Precedence (low → high):
    DEFAULT → CONFIG_FILE → DATABASE → ENV_OVERRIDE
"""

from __future__ import annotations

from agent.modules.settings.domain.ports import SettingsRepository
from agent.modules.settings.domain.settings_value import (
    AppSettingsData,
    SettingsSource,
    SettingsValue,
)


class SettingsService:
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
            merged.update(repo.get_all())
        return merged

    def list_all_by_source(self) -> dict[str, list[SettingsValue]]:
        """Return every value from every source, grouped by key.

        Useful for the dashboard to show where each value comes from.
        """
        by_key: dict[str, list[SettingsValue]] = {}
        for repo in self._repos:
            for key, val in repo.get_all().items():
                by_key.setdefault(key, []).append(val)
        return by_key

    def get_app_settings(self) -> AppSettingsData:
        """Build a consolidated ``AppSettingsData`` from merged sources."""
        merged = self.list_all()

        def _val(key: str, default: object) -> object:
            sv = merged.get(key)
            return sv.value if sv is not None else default

        defaults = AppSettingsData()

        service_boot_flags = dict(defaults.service_boot_flags)
        for flag_key in list(service_boot_flags):
            canon = f"channels.{flag_key}.enabled"
            sv = merged.get(canon)
            if sv is not None:
                service_boot_flags[flag_key] = bool(sv.value)

        return AppSettingsData(
            host=str(_val("host", defaults.host)),
            port=int(_val("port", defaults.port)),  # type: ignore[arg-type]
            enable_web=bool(_val("enable_web", defaults.enable_web)),
            enable_api=bool(_val("enable_api", defaults.enable_api)),
            enable_dashboard=bool(_val("enable_dashboard", defaults.enable_dashboard)),
            service_boot_flags=service_boot_flags,
        )

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


__all__ = ["SettingsService"]
