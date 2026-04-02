import os
from dataclasses import dataclass
from pathlib import Path

from agent.shared.infrastructure.config_file import DEFAULT_CONFIG_PATH, coerce_bool, load_flat_config_file


def parse_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return coerce_bool(value)


@dataclass(frozen=True, slots=True)
class BootstrapConfig:
    host: str
    port: int
    enable_web: bool
    enable_api: bool
    enable_dashboard: bool


def load_bootstrap_config(path: Path | None = None) -> BootstrapConfig:
    """Build bootstrap config from defaults, config file, and env vars."""
    flat_config = load_flat_config_file(path or DEFAULT_CONFIG_PATH)

    default_config = BootstrapConfig(
        host="0.0.0.0",
        port=8000,
        enable_web=True,
        enable_api=True,
        enable_dashboard=True,
    )

    host = str(flat_config.get("host", default_config.host))
    port = int(flat_config.get("port", default_config.port))
    enable_web = coerce_bool(flat_config.get("enable_web", default_config.enable_web))
    enable_api = coerce_bool(flat_config.get("enable_api", default_config.enable_api))
    enable_dashboard = coerce_bool(
        flat_config.get("enable_dashboard", default_config.enable_dashboard)
    )

    return BootstrapConfig(
        host=os.getenv("HOST", host),
        port=int(os.getenv("PORT", str(port))),
        enable_web=parse_bool_env("ENABLE_WEB", enable_web),
        enable_api=parse_bool_env("ENABLE_API", enable_api),
        enable_dashboard=parse_bool_env("ENABLE_DASHBOARD", enable_dashboard),
    )


__all__ = ["BootstrapConfig", "DEFAULT_CONFIG_PATH", "load_bootstrap_config", "parse_bool_env"]
