import os
from dataclasses import dataclass

from agent.modules.settings.public import get_app_settings as _get_module_settings


def parse_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class AppSettings:
    host: str
    port: int
    enable_web: bool
    enable_api: bool
    enable_dashboard: bool
    service_boot_flags: dict[str, bool]

    @classmethod
    def from_env(cls) -> "AppSettings":
        """Build settings by delegating to the settings module.

        The settings module merges defaults, config file, and env vars.
        This method converts the module's ``AppSettingsData`` into the
        bootstrap ``AppSettings`` dataclass for backward compatibility.
        """
        data = _get_module_settings()
        return cls(
            host=data.host,
            port=data.port,
            enable_web=data.enable_web,
            enable_api=data.enable_api,
            enable_dashboard=data.enable_dashboard,
            service_boot_flags=dict(data.service_boot_flags),
        )
