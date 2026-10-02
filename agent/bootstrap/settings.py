from dataclasses import dataclass, field

from agent.shared.config import get_config_service


CONFIG_KEY_HOST = "host"
CONFIG_KEY_PORT = "port"
CONFIG_KEY_ENABLE_WEB = "enable_web"
CONFIG_KEY_ENABLE_API = "enable_api"
CONFIG_KEY_ENABLE_DASHBOARD = "enable_dashboard"
CONFIG_KEY_CORS_ORIGINS = "security.cors_origins"
CONFIG_KEY_CSRF_PROTECTION_ENABLED = "security.csrf_protection_enabled"
CONFIG_KEY_TRAY_ENABLED = "tray.enabled"
CONFIG_KEY_TRAY_AUTOSTART = "tray.autostart"


@dataclass(frozen=True, slots=True)
class BootstrapConfig:
    host: str
    port: int
    enable_web: bool
    enable_api: bool
    enable_dashboard: bool
    cors_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:4141", "http://127.0.0.1:4141"]
    )
    csrf_protection_enabled: bool = True
    tray_enabled: bool = True
    tray_autostart: bool = False


def load_bootstrap_config(config_service=None) -> BootstrapConfig:
    """Build bootstrap config from injected service or active container."""
    config = config_service if config_service is not None else get_config_service()
    config.ensure_default_files()

    # Load CORS origins - default to localhost only for security
    cors_origins_raw = config.get(CONFIG_KEY_CORS_ORIGINS, ["http://localhost:4141", "http://127.0.0.1:4141"])
    if isinstance(cors_origins_raw, str):
        cors_origins = [origin.strip() for origin in cors_origins_raw.split(",") if origin.strip()]
    elif isinstance(cors_origins_raw, list):
        cors_origins = [str(origin).strip() for origin in cors_origins_raw if str(origin).strip()]
    else:
        cors_origins = ["http://localhost:4141", "http://127.0.0.1:4141"]

    return BootstrapConfig(
        host=config.get_str(CONFIG_KEY_HOST, "localhost"),
        port=config.get_int(CONFIG_KEY_PORT, 4141),
        enable_web=config.get_bool(CONFIG_KEY_ENABLE_WEB, True),
        enable_api=config.get_bool(CONFIG_KEY_ENABLE_API, True),
        enable_dashboard=config.get_bool(CONFIG_KEY_ENABLE_DASHBOARD, True),
        cors_origins=cors_origins,
        csrf_protection_enabled=config.get_bool(CONFIG_KEY_CSRF_PROTECTION_ENABLED, True),
        tray_enabled=config.get_bool(CONFIG_KEY_TRAY_ENABLED, True),
        tray_autostart=config.get_bool(CONFIG_KEY_TRAY_AUTOSTART, False),
    )


__all__ = ["BootstrapConfig", "load_bootstrap_config"]
