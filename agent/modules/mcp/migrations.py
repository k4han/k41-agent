from __future__ import annotations

import logging

from sqlalchemy import create_engine, inspect

from agent.modules.mcp.install_repository import McpInstallRepository
from agent.modules.mcp.repository import ConfigMcpServerRepository
from agent.shared.infrastructure.db import Base
from agent.shared.infrastructure.db.engine import _normalize_url_to_sync

logger = logging.getLogger(__name__)


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
                if card.mcp_servers:
                    for server_name in card.mcp_servers:
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
