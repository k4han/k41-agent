"""Slim runtime startup for the interactive CLI via AppContainer.

Brings up only the pieces required to run an agent locally:
persistence, workflows, skills, and the scheduler. Web hosts and
managed channels (telegram/discord) are intentionally skipped.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class CLIRuntime:
    """Lifecycle owner for resources used by the interactive CLI."""

    def __init__(self, container=None) -> None:
        from agent.bootstrap.container import create_app_container

        self.container = container or create_app_container()
        self._started = False
        self._activated = False
        self._previous_active = None

    async def startup(self) -> None:
        if self._started:
            return
        from agent.bootstrap.container import set_active_container
        from agent.modules.scheduler import initialize_scheduler
        from agent.modules.skills import reload_skills
        from agent.modules.workflows import register_builtin_workflows

        logger.info("Initializing CLI runtime...")
        self._previous_active = set_active_container(self.container)
        self._activated = True
        try:
            await self.container.initialize_persistence()
            register_builtin_workflows()
            reload_skills()
            await initialize_scheduler(container=self.container)
            self._started = True
        except Exception:
            logger.exception("CLI startup failed.")
            await self.shutdown()
            raise
        logger.info("CLI runtime ready.")

    async def shutdown(self) -> None:
        """Release every CLI resource, even when individual steps fail.

        Steps are attempted independently so a failing scheduler stop can
        never skip closing persistence, and restoring the previously active
        container always runs in ``finally``.
        """
        if not self._started and not self._activated:
            return
        from agent.bootstrap.container import set_active_container
        from agent.modules.scheduler import stop_scheduler

        logger.info("Stopping CLI runtime...")
        errors: list[BaseException] = []
        try:
            await self._attempt(
                errors,
                "stop background scheduler",
                lambda: stop_scheduler(container=self.container),
            )
            await self._attempt(
                errors, "close persistence", self.container.close_persistence
            )
        finally:
            set_active_container(self._previous_active)
            self._previous_active = None
            self._started = False
            self._activated = False
        if errors:
            logger.warning(
                "Shutdown completed with %d failed step(s): %s",
                len(errors),
                "; ".join(f"{type(error).__name__}: {error}" for error in errors),
            )
        logger.info("CLI runtime stopped.")

    @staticmethod
    async def _attempt(
        errors: list[BaseException], label: str, step: Callable[[], Awaitable[None]]
    ) -> None:
        """Run one shutdown step, collecting its error instead of raising."""
        try:
            await step()
        except Exception as exc:
            errors.append(exc)
            logger.warning("Shutdown step '%s' failed: %s", label, exc, exc_info=True)


__all__ = ["CLIRuntime"]
