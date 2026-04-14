from dataclasses import dataclass

from agent.shared.config import get_config_service


CONFIG_KEY_HOST = "host"
CONFIG_KEY_PORT = "port"
CONFIG_KEY_ENABLE_WEB = "enable_web"
CONFIG_KEY_ENABLE_API = "enable_api"
CONFIG_KEY_ENABLE_DASHBOARD = "enable_dashboard"


@dataclass(frozen=True, slots=True)
class BootstrapConfig:
    host: str
    port: int
    enable_web: bool
    enable_api: bool
    enable_dashboard: bool


def load_bootstrap_config() -> BootstrapConfig:
    """Build bootstrap config from config service."""
    config = get_config_service()

    return BootstrapConfig(
        host=config.get_str(CONFIG_KEY_HOST, "localhost"),
        port=config.get_int(CONFIG_KEY_PORT, 8000),
        enable_web=config.get_bool(CONFIG_KEY_ENABLE_WEB, True),
        enable_api=config.get_bool(CONFIG_KEY_ENABLE_API, True),
        enable_dashboard=config.get_bool(CONFIG_KEY_ENABLE_DASHBOARD, True),
    )


__all__ = ["BootstrapConfig", "load_bootstrap_config"]
