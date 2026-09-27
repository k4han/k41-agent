import logging
import os
from importlib import import_module
from pathlib import Path
from typing import Any

# Keep package import light: heavy submodules (runtime, settings) are only
# loaded on first attribute access so that `k41 --help` and other commands
# start instantly instead of pulling in langchain/channels/sqlalchemy.
_LAZY_EXPORTS = {
    "AppRuntime": "agent.bootstrap.runtime",
    "BUILTIN_CHANNEL_DESCRIPTORS": "agent.bootstrap.runtime",
    "ChannelDescriptor": "agent.bootstrap.runtime",
    "BootstrapConfig": "agent.bootstrap.settings",
    "load_bootstrap_config": "agent.bootstrap.settings",
}

_APP_EXPORTS = {"app", "create_app", "main", "run", "settings"}


def load_env_files() -> None:
    """Load local ``.env`` files into the process environment.

    Runs once at package import so every entry point (CLI server, tray,
    ``main.py``) reads the same values. Files are searched in the working
    directory first, then in the agent home. Existing environment variables
    are never overwritten, so shell/service exports always win.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        logging.getLogger(__name__).warning(
            "python-dotenv is not installed; skipping .env loading."
        )
        return

    load_dotenv(Path.cwd() / ".env")
    agent_home = (
        os.environ.get("K41_AGENT_HOME")
        or os.environ.get("AGENT_HOME")
        or str(Path.home() / ".k41-agent")
    )
    load_dotenv(Path(agent_home) / ".env")


load_env_files()


def __getattr__(name: str) -> Any:
    if name in _APP_EXPORTS:
        app_module = import_module("agent.bootstrap.app")
        return getattr(app_module, name)
    if name in _LAZY_EXPORTS:
        module = import_module(_LAZY_EXPORTS[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(_LAZY_EXPORTS) | _APP_EXPORTS)


__all__ = [
    "app",
    "create_app",
    "load_env_files",
    "main",
    "run",
    "settings",
    "AppRuntime",
    "BUILTIN_CHANNEL_DESCRIPTORS",
    "ChannelDescriptor",
    "BootstrapConfig",
    "load_bootstrap_config",
]
