"""Application layer for the agents module."""

from agent.modules.agents.application.service import (
    AgentCatalogService,
    get_catalog_service,
)

__all__ = ["AgentCatalogService", "get_catalog_service"]
