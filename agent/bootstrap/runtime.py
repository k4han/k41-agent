import logging

from agent.bootstrap.settings import BootstrapConfig
from agent.modules.channels import (
    BUILTIN_CHANNEL_SPECS,
    ChannelManager,
    ChannelSpec,
    register_channels,
    start_enabled_channels,
    stop_all_channels,
)
from agent.shared.config import RuntimeSettings
from agent.modules.workflows import (
    close_checkpointer,
    initialize_checkpointer,
    register_builtin_workflows,
)
from agent.modules.scheduler import initialize_scheduler, stop_scheduler
from agent.modules.skills import reload_skills
from agent.modules.usage import prune_usage_events
from agent.modules.agent_runtime import get_background_task_manager
from agent.shared.infrastructure.db import Base, load_orm_models
from agent.shared.infrastructure.db.engine import (
    close_async_engine,
    get_database_url,
    initialize_async_engine,
)
from agent.modules.workspaces import migrate_workspace_tables

logger = logging.getLogger(__name__)


async def initialize_persistence() -> None:
    """Initialize SQLAlchemy async engine and LangGraph checkpointer."""
    load_orm_models()
    await initialize_async_engine(metadata=Base.metadata)

    migrate_workspace_tables(get_database_url())
    await prune_usage_events()
    await initialize_checkpointer()



async def close_persistence() -> None:
    """Close persistence resources for clean shutdowns."""
    await close_checkpointer()
    await close_async_engine()


class AppRuntime:
    """Own shared resources and managed background channels."""

    def __init__(
        self,
        bootstrap_config: BootstrapConfig,
        runtime_settings: RuntimeSettings,
    ):
        self.bootstrap_config = bootstrap_config
        self.runtime_settings = runtime_settings
        self.channel_manager = ChannelManager()
        self._channels_registered = False
        self._persistence_ready = False
        self._started = False

    async def startup(self) -> None:
        if self._started:
            return

        try:
            if not self._persistence_ready:
                logger.info("Initializing persistence...")
                await initialize_persistence()
                self._persistence_ready = True

            logger.info("Building workflows...")
            register_builtin_workflows()

            logger.info("Discovering skills...")
            reload_skills()

            self._register_channels()
            await self._start_enabled_channels()

            logger.info("Starting background scheduler...")
            await initialize_scheduler()

            logger.info("Restoring background task history...")
            await get_background_task_manager().restore_from_persistence()

            self._started = True
            logger.info("Application runtime is ready.")
        except Exception:
            logger.exception("Application startup failed.")
            await self.shutdown()
            raise

    async def shutdown(self) -> None:
        if self.channel_manager.names():
            logger.info("Stopping managed channels...")
            await stop_all_channels(self.channel_manager)

        logger.info("Stopping background scheduler...")
        await stop_scheduler()

        if self._persistence_ready:
            logger.info("Closing persistence...")
            await close_persistence()
            self._persistence_ready = False

        self._started = False
        logger.info("Application runtime stopped.")

    def _register_channels(self) -> None:
        if self._channels_registered:
            return

        logger.info("Registering configured channels...")
        register_channels(self.channel_manager, BUILTIN_CHANNEL_SPECS)
        self._channels_registered = True

    async def _start_enabled_channels(self) -> None:
        await start_enabled_channels(
            self.channel_manager,
            self.runtime_settings.channel_enabled,
            BUILTIN_CHANNEL_SPECS,
        )

