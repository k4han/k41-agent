from __future__ import annotations

from importlib import metadata
from pathlib import Path
import tomllib


PACKAGE_NAME = "k41-agent"
DEFAULT_VERSION = "0.0.0"


def get_app_version() -> str:
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return _read_project_version()


def _read_project_version() -> str:
    project_file = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        data = tomllib.loads(project_file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return DEFAULT_VERSION
    version = data.get("project", {}).get("version")
    return str(version) if version else DEFAULT_VERSION


APP_VERSION = get_app_version()


__all__ = ["APP_VERSION", "DEFAULT_VERSION", "PACKAGE_NAME", "get_app_version"]
