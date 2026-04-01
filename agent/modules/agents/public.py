"""Public facade for the agents module.

Other modules should import from here, not from internal packages.
"""

from agent.modules.agents.application.service import (
    AgentCatalogService,
    get_catalog_service,
)
from agent.modules.agents.domain.subagent import AgentConfig
from agent.modules.agents.infrastructure.repository import (
    load_agents_from_dir,
    reload_agents,
)

__all__ = [
    "AgentConfig",
    "AgentCatalogService",
    "get_catalog_service",
    "reload_agents",
    "load_agents_from_dir",
]
