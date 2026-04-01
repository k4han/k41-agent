"""Scan agent MD files and load configs into the catalog.

Graphs are shared templates; agent-specific config is resolved at runtime.
"""

from __future__ import annotations

import logging

from agent.modules.agents.public import get_catalog_service, load_agents_from_dir

logger = logging.getLogger(__name__)


def scan_agents_from_md() -> None:
    """Load agent configs from MD files into the catalog.

    Must be called AFTER register_builtin_workflows() so that base graph types
    are already registered.
    """
    agents = load_agents_from_dir()
    if agents:
        logger.info("Loaded %d agent(s) from MD files into catalog.", len(agents))
    else:
        logger.info("No agents loaded from MD files.")


__all__ = ["scan_agents_from_md"]
