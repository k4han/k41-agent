"""Repository that reads MCP server configs from the config service."""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.exc import OperationalError, SQLAlchemyError

from agent.modules.mcp.models import MCPServerConfig, MCPTransport
from agent.shared.config import get_config_service
from agent.shared.infrastructure.config_file import coerce_bool
from agent.shared.infrastructure.parsing import parse_string_or_list

logger = logging.getLogger(__name__)


_PROVIDER_KEY_PATTERN = re.compile(
    r"^mcp\.servers\.([A-Za-z0-9_-]+)\.(.+)$"
)


def parse_mcp_server_key(key: str) -> tuple[str, str] | None:
    """Parse ``mcp.servers.<name>.<field>`` into (name, field)."""
    match = _PROVIDER_KEY_PATTERN.match(key)
    if not match:
        return None
    return match.group(1), match.group(2)


def _normalize_server_name(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _extract_server_entries(flat_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Group flat ``mcp.servers.<name>.<field>`` keys back into per-server dicts."""
    servers: dict[str, dict[str, Any]] = {}
    for key, value in flat_config.items():
        parsed = parse_mcp_server_key(key)
        if parsed is None:
            continue

        raw_name, field_path = parsed
        normalized = _normalize_server_name(raw_name)
        if not normalized:
            continue

        entry = servers.setdefault(
            normalized,
            {"_name": raw_name.strip() or normalized, "env": {}, "headers": {}},
        )

        if field_path.startswith("env."):
            env_key = field_path[4:]
            if env_key:
                entry["env"][env_key] = "" if value is None else str(value)
            continue
        if field_path.startswith("headers."):
            header_key = field_path[8:]
            if header_key:
                entry["headers"][header_key] = "" if value is None else str(value)
            continue

        entry[field_path] = value

    return servers


def _resolve_transport(value: Any) -> MCPTransport:
    text = str(value or "").strip().lower()
    if text in ("streamable_http", "http", "https"):
        return MCPTransport.HTTP
    return MCPTransport.STDIO


def _build_server_config(
    server_key: str,
    values: dict[str, Any],
) -> MCPServerConfig:
    name = str(values.get("_name") or server_key).strip() or server_key
    transport = _resolve_transport(values.get("transport"))
    enabled = coerce_bool(values.get("enabled", True))

    command = str(values.get("command") or "").strip()
    args = tuple(parse_string_or_list(values.get("args", [])))
    env = {
        str(key): str(val) if val is not None else ""
        for key, val in values.get("env", {}).items()
    }

    url = str(values.get("url") or "").strip()
    headers = {
        str(key): str(val) if val is not None else ""
        for key, val in values.get("headers", {}).items()
    }

    return MCPServerConfig(
        name=name,
        transport=transport,
        command=command,
        args=args,
        env=env,
        url=url,
        headers=headers,
        enabled=enabled,
    )


def _config_fingerprint(flat_config: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (key, repr(value))
            for key, value in flat_config.items()
            if key.startswith("mcp.servers.")
        )
    )


# Module-level version counter that the install repository bumps on every
# mutation. The config repository includes this in its cache key so any
# out-of-band DB change invalidates the in-memory cache without a manual
# ``reload()`` call (e.g. background migration, tests).
_DB_REVISION = 0


def bump_db_revision() -> None:
    """Invalidate the config cache by bumping the DB revision counter."""
    global _DB_REVISION
    _DB_REVISION += 1


def _db_revision() -> int:
    return _DB_REVISION


class ConfigMcpServerRepository:
    """Resolve MCP server configs from DB installs with legacy config fallback."""

    def __init__(self) -> None:
        self._cache: tuple[
            dict[str, MCPServerConfig],
            tuple[tuple[str, str], ...],
            int,
        ] | None = None

    def reload(self) -> None:
        self._cache = None
        bump_db_revision()

    def _load_legacy(self) -> dict[str, MCPServerConfig]:
        config = get_config_service()
        flat_config = config.get_all()

        entries = _extract_server_entries(flat_config)
        servers: dict[str, MCPServerConfig] = {}
        for key, values in entries.items():
            servers[key] = _build_server_config(key, values)
        return servers

    def _load_db(self) -> dict[str, MCPServerConfig]:
        from agent.modules.mcp.install_repository import McpInstallRepository

        try:
            repo = McpInstallRepository()
        except (OperationalError, SQLAlchemyError) as exc:
            logger.warning("Falling back to legacy MCP config (DB unavailable): %s", exc)
            return {}
        try:
            try:
                configs = repo.list_server_configs()
            finally:
                repo.close()
        except (OperationalError, SQLAlchemyError) as exc:
            logger.warning("Falling back to legacy MCP config (DB unavailable): %s", exc)
            return {}
        return {
            _normalize_server_name(config.name): config
            for config in configs
        }

    def _load(self) -> dict[str, MCPServerConfig]:
        config = get_config_service()
        flat_config = config.get_all()
        config_fp = _config_fingerprint(flat_config)
        db_fp = _db_revision()
        if self._cache is not None and self._cache[1] == config_fp and self._cache[2] == db_fp:
            return self._cache[0]

        servers = self._load_legacy()
        servers.update(self._load_db())

        self._cache = (servers, config_fp, db_fp)
        return servers

    def list_servers(self) -> list[MCPServerConfig]:
        return list(self._load().values())

    def get_server(self, name: str) -> MCPServerConfig:
        servers = self._load()
        normalized = _normalize_server_name(name)
        if normalized not in servers:
            raise KeyError(f"MCP server not found: {name!r}")
        return servers[normalized]


__all__ = [
    "ConfigMcpServerRepository",
    "bump_db_revision",
    "parse_mcp_server_key",
]
