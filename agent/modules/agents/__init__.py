"""Public facade for the agents module.

Other modules should import from here, not from internal packages.
"""

from agent.modules.agents.service import (
    AgentCatalogService,
    get_catalog_service,
)
from agent.modules.agents.models import AgentCard, AgentConfig
from agent.modules.agents.repository import (
    load_agents_from_dir,
    reload_agents,
)


def resolve_catalog_agent_name(*candidates: str | None) -> str | None:
    """Return the first existing agent name from candidates, else None."""
    catalog = get_catalog_service()
    for candidate in candidates:
        name = (candidate or "").strip()
        if not name:
            continue
        if catalog.get_agent(name) is not None:
            return name
    return None


__all__ = [
    "AgentConfig",
    "AgentCard",
    "AgentCatalogService",
    "get_catalog_service",
    "reload_agents",
    "load_agents_from_dir",
    "resolve_catalog_agent_name",
]
