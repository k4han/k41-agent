from __future__ import annotations

import logging

from sqlalchemy import create_engine, inspect

from agent.modules.mcp.install_repository import McpInstallRepository
from agent.modules.mcp.repository import ConfigMcpServerRepository
from agent.shared.infrastructure.db import Base
from agent.shared.infrastructure.db.engine import _normalize_url_to_sync
from agent.shared.infrastructure.parsing import parse_string_or_list

logger = logging.getLogger(__name__)


def _legacy_mcp_servers_from_card(card) -> list[str] | None:
    """Read legacy mcp_servers from raw frontmatter for upgrade path."""
    legacy = getattr(card, "mcp_servers", None)
    if isinstance(legacy, list):
        return parse_string_or_list(legacy)
    path = getattr(card, "path", "")
    if not path:
        return None
    try:
        from pathlib import Path

        import yaml

        text = Path(str(path)).read_text(encoding="utf-8")
        if not text.lstrip().startswith("---"):
            return None
        parts = text.split("---", 2)
        if len(parts) < 3:
            return None
        data = yaml.safe_load(parts[1])
        if not isinstance(data, dict):
            return None
        raw = data.get("mcp_servers")
        if raw is None:
            return None
        if not isinstance(raw, (str, list)):
            return None
        return parse_string_or_list(raw)
    except Exception as exc:
        logger.debug("Failed to read legacy mcp_servers from %s: %s", path, exc)
        return None


def migrate_mcp_tables(database_url: str) -> None:
    """Create MCP install tables and seed legacy configuration."""
    engine = create_engine(_normalize_url_to_sync(database_url), echo=False)
    try:
        Base.metadata.create_all(engine)
        inspector = inspect(engine)
        if "mcp_server_installs" not in inspector.get_table_names():
            return
    finally:
        engine.dispose()

    repo = McpInstallRepository(database_url)
    try:
        legacy_repo = ConfigMcpServerRepository()
        for config in legacy_repo._load_legacy().values():
            repo.seed_server_install(server_name=config.name, config=config)

        try:
            from agent.modules.agents import get_catalog_service

            catalog = get_catalog_service()
            all_installs = repo.list_all_installs()
            for card in catalog.list_agent_cards():
                if not card.valid:
                    continue
                legacy_servers = getattr(card, "mcp_servers", None)
                if legacy_servers is None:
                    legacy_servers = _legacy_mcp_servers_from_card(card)
                if legacy_servers:
                    for server_name in legacy_servers:
                        repo.seed_agent_install(
                            agent_name=card.name,
                            server_name=server_name,
                        )
                    continue
                if card.name == "default":
                    # The legacy ``default`` chat agent auto-included every
                    # installed MCP server. Seed that binding so the agent
                    # keeps working out of the box.
                    for install in all_installs:
                        repo.seed_agent_install(
                            agent_name=card.name,
                            server_name=str(install.get("server_name") or ""),
                        )
        except Exception as exc:
            logger.warning("Failed to seed MCP agent bindings: %s", exc)
    finally:
        repo.close()


__all__ = ["migrate_mcp_tables"]
