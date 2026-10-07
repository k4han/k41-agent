import logging
from collections.abc import Awaitable, Callable

from agent.bootstrap.container import (
    AppContainer,
    create_app_container,
    get_active_container,
    set_active_container,
)
from agent.modules.channels import (
    BUILTIN_CHANNEL_DESCRIPTORS,
    ChannelDescriptor,
    register_channels,
    start_enabled_channels,
    stop_all_channels,
)

logger = logging.getLogger(__name__)


def _cleanup_update_state() -> None:
    from agent.bootstrap.update import UpdateError, resolve_managed_install
    from agent.bootstrap.update_state import UpdateBusyError, update_lock

    try:
        install = resolve_managed_install()
    except UpdateError:
        return
    try:
        # Lock acquisition cleans abandoned files without touching an active writer.
        with update_lock(install.agent_home):
            pass
    except UpdateBusyError:
        pass
    except OSError as exc:
        logger.warning("Could not clean up update state: %s", exc)

__all__ = [
    "AppRuntime",
    "BUILTIN_CHANNEL_DESCRIPTORS",
    "ChannelDescriptor",
    "close_persistence",
    "initialize_persistence",
]


async def initialize_persistence(container=None) -> None:
    """Initialize persistence in container scope."""
    from agent.bootstrap.container import require_active_container

    await require_active_container(container).initialize_persistence()


async def close_persistence(container=None) -> None:
    """Close persistence in container scope."""
    from agent.bootstrap.container import require_active_container

    await require_active_container(container).close_persistence()


class AppRuntime:
    """Own application lifecycle via AppContainer (no settings snapshot)."""

    def __init__(
        self,
        bootstrap_config=None,
        runtime_settings=None,
        container: AppContainer | None = None,
    ):
        if container is None:
            active = get_active_container()
            config_service = active.config_service if active is not None else None
            container = create_app_container(
                bootstrap_config=bootstrap_config,
                config_service=config_service,
            )
        self.container = container
        if bootstrap_config is not None:
            self.container.bootstrap_config = bootstrap_config
        # Legacy snapshot override for tests migrating off direct RuntimeSettings.
        self._runtime_settings_override = runtime_settings
        self._channels_registered = False
        self._started = False
        self._activated = False
        self._previous_active = None

    @property
    def bootstrap_config(self):
        return self.container.bootstrap_config

    @bootstrap_config.setter
    def bootstrap_config(self, value) -> None:
        self.container.bootstrap_config = value

    @property
    def runtime_settings(self):
        """Live settings, never snapshotted (override wins in legacy tests)."""
        if self._runtime_settings_override is not None:
            return self._runtime_settings_override
        return self.container.runtime_settings

    @runtime_settings.setter
    def runtime_settings(self, value) -> None:
        self._runtime_settings_override = value

    @property
    def channel_manager(self):
        return self.container.channel_manager

    @property
    def _persistence_ready(self) -> bool:
        return bool(getattr(self.container, "_persistence_ready", False))

    async def startup(self) -> None:
        if self._started:
            return
        self._previous_active = set_active_container(self.container)
        self._activated = True
        try:
            _cleanup_update_state()
            if not self.container._persistence_ready:
                logger.info("Initializing persistence...")
                await self.container.initialize_persistence()

            logger.info("Ensuring provider catalog...")
            from agent.modules.providers import ensure_catalog_available

            catalog_ready, catalog_message = await ensure_catalog_available()
            if catalog_ready:
                logger.info(catalog_message)
            else:
                logger.warning(catalog_message)

            logger.info("Building workflows...")
            from agent.modules.workflows import register_builtin_workflows

            register_builtin_workflows()

            logger.info("Discovering skills...")
            from agent.modules.skills import reload_skills

            reload_skills()

            self._register_channels()
            await self._start_enabled_channels()

            logger.info("Starting background scheduler...")
            from agent.modules.scheduler import initialize_scheduler

            await initialize_scheduler()

            logger.info("Restoring background task history...")
            await self.container.task_manager.restore_from_persistence()

            logger.info("Pruning orphaned GitHub worktrees...")
            try:
                pruned = await self.container.github_service.prune_orphaned_worktrees()
                if pruned:
                    logger.info("Pruned %d orphaned GitHub worktrees.", pruned)
            except Exception as exc:
                logger.warning("Failed to prune orphaned GitHub worktrees: %s", exc)

            logger.info("Cleaning up orphaned temporary workspaces...")
            try:
                from agent.modules.workspaces import cleanup_orphaned_temp_workspaces

                cleaned = await cleanup_orphaned_temp_workspaces()
                if cleaned.get("directories_removed") or cleaned.get("records_removed"):
                    logger.info(
                        "Removed %d orphaned temp workspace directories and %d records.",
                        cleaned["directories_removed"],
                        cleaned["records_removed"],
                    )
            except Exception as exc:
                logger.warning("Failed to clean up orphaned temp workspaces: %s", exc)

            logger.info("Starting workspace background services...")
            from agent.modules.workspaces import start_enabled_workspace_background_services

            await start_enabled_workspace_background_services()

            self._started = True
            logger.info("Application runtime is ready.")
        except Exception:
            logger.exception("Application startup failed.")
            await self.shutdown()
            raise

    async def shutdown(self) -> None:
        """Release every runtime resource, even when individual steps fail.

        Steps are attempted in order through :meth:`_attempt` so one failing
        component can never skip cleanup of the remaining ones; errors are
        collected, logged, and summarized instead of aborting the chain.
        """
        if not self._started and not self._activated:
            return
        errors: list[BaseException] = []
        try:
            await self._attempt(errors, "stop managed channels", self._stop_channels_step)
            await self._attempt(errors, "stop background scheduler", self._stop_scheduler_step)
            await self._attempt(
                errors, "stop workspace background services", self._stop_workspace_services_step
            )
            await self._attempt(errors, "close persistence", self._close_persistence_step)
        finally:
            self._started = False
            if self._activated:
                set_active_container(self._previous_active)
                self._previous_active = None
                self._activated = False
            logger.info("Application runtime stopped.")
        if errors:
            logger.warning(
                "Shutdown completed with %d failed step(s): %s",
                len(errors),
                "; ".join(f"{type(error).__name__}: {error}" for error in errors),
            )

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

    async def _stop_channels_step(self) -> None:
        if self.channel_manager.names():
            logger.info("Stopping managed channels...")
            await stop_all_channels(self.channel_manager)

    async def _stop_scheduler_step(self) -> None:
        logger.info("Stopping background scheduler...")
        from agent.modules.scheduler import stop_scheduler

        await stop_scheduler()

    async def _stop_workspace_services_step(self) -> None:
        logger.info("Stopping workspace background services...")
        from agent.modules.workspaces import stop_workspace_background_services

        await stop_workspace_background_services()

    async def _close_persistence_step(self) -> None:
        if self.container._persistence_ready:
            logger.info("Closing persistence...")
            await self.container.close_persistence()

    def _register_channels(self) -> None:
        if self._channels_registered:
            return
        logger.info("Registering configured channels...")
        register_channels(self.channel_manager, BUILTIN_CHANNEL_DESCRIPTORS)
        self._channels_registered = True

    async def _start_enabled_channels(self) -> None:
        await start_enabled_channels(
            self.channel_manager,
            self.runtime_settings.channel_enabled,
            BUILTIN_CHANNEL_DESCRIPTORS,
        )
