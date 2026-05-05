"""Slim runtime startup for the interactive CLI.

Brings up only the pieces required to run an agent locally:
persistence, workflows, skills, and the scheduler. Web hosts and
managed channels (telegram/discord) are intentionally skipped.
"""

from __future__ import annotations

import logging

from agent.modules.scheduler import initialize_scheduler, stop_scheduler
from agent.modules.skills import reload_skills
from agent.modules.workflows import (
    close_checkpointer,
    initialize_checkpointer,
    register_builtin_workflows,
)
from agent.shared.infrastructure.db import Base, load_orm_models
from agent.shared.infrastructure.db.engine import (
    close_async_engine,
    initialize_async_engine,
)

logger = logging.getLogger(__name__)


class CLIRuntime:
    """Lifecycle owner for resources used by the interactive CLI."""

    def __init__(self) -> None:
        self._started = False

    async def startup(self) -> None:
        if self._started:
            return

        logger.info("Initializing CLI runtime...")
        load_orm_models()
        await initialize_async_engine(metadata=Base.metadata)
        await initialize_checkpointer()
        register_builtin_workflows()
        reload_skills()
        await initialize_scheduler()
        self._started = True
        logger.info("CLI runtime ready.")

    async def shutdown(self) -> None:
        if not self._started:
            return

        logger.info("Stopping CLI runtime...")
        await stop_scheduler()
        await close_checkpointer()
        await close_async_engine()
        self._started = False
        logger.info("CLI runtime stopped.")


__all__ = ["CLIRuntime"]
