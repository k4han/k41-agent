import asyncio
import logging
from collections.abc import Awaitable, Callable
from enum import Enum

logger = logging.getLogger(__name__)

ChannelRunner = Callable[[], Awaitable[None]]


class ChannelStatus(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    STARTING = "starting"
    STOPPING = "stopping"
    ERROR = "error"


class ManagedChannel:
    """Represent a background channel managed within the app runtime."""

    def __init__(self, name: str, runner: ChannelRunner):
        self.name = name
        self.runner = runner
        self.status = ChannelStatus.STOPPED
        self.error: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self.status in (ChannelStatus.STARTING, ChannelStatus.RUNNING):
                logger.info("[%s] Already active with status=%s.", self.name, self.status)
                return

            self.status = ChannelStatus.STARTING
            self.error = None
            self._task = asyncio.create_task(self._run(), name=f"channel:{self.name}")
            logger.info("[%s] Starting...", self.name)

    async def stop(self) -> None:
        async with self._lock:
            if self.status in (ChannelStatus.STOPPED, ChannelStatus.STOPPING):
                logger.info("[%s] Already inactive with status=%s.", self.name, self.status)
                return

            self.status = ChannelStatus.STOPPING
            task = self._task
            logger.info("[%s] Stopping...", self.name)

        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        async with self._lock:
            self.status = ChannelStatus.STOPPED
            self._task = None
            logger.info("[%s] Stopped.", self.name)

    async def _run(self) -> None:
        async with self._lock:
            if self.status == ChannelStatus.STARTING:
                self.status = ChannelStatus.RUNNING
                logger.info("[%s] Running.", self.name)

        try:
            await self.runner()
        except asyncio.CancelledError:
            logger.info("[%s] Cancelled.", self.name)
            raise
        except Exception as exc:
            async with self._lock:
                self.error = str(exc)
                self.status = ChannelStatus.ERROR
            logger.exception("[%s] Error while running channel.", self.name)
        finally:
            async with self._lock:
                self._task = None
                if self.status not in (ChannelStatus.STOPPING, ChannelStatus.ERROR):
                    self.status = ChannelStatus.STOPPED

    def info(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "status": self.status,
            "error": self.error,
        }


class ChannelManager:
    """Manage background channels owned by a specific app runtime."""

    def __init__(self) -> None:
        self._channels: dict[str, ManagedChannel] = {}

    def register(self, name: str, runner: ChannelRunner) -> None:
        if name in self._channels:
            raise ValueError(f"Channel '{name}' is already registered.")
        self._channels[name] = ManagedChannel(name, runner)
        logger.info("[ChannelManager] Registered: %s", name)

    async def start(self, name: str) -> None:
        await self._get_or_raise(name).start()

    async def stop(self, name: str) -> None:
        await self._get_or_raise(name).stop()

    async def start_many(self, names: list[str] | tuple[str, ...]) -> None:
        await asyncio.gather(*(self.start(name) for name in names))

    async def start_all(self) -> None:
        await asyncio.gather(*(channel.start() for channel in self._channels.values()))

    async def stop_all(self) -> None:
        for channel in reversed(tuple(self._channels.values())):
            await channel.stop()

    def status_all(self) -> list[dict[str, str | None]]:
        return [channel.info() for channel in self._channels.values()]

    def status(self, name: str) -> dict[str, str | None]:
        return self._get_or_raise(name).info()

    def names(self) -> list[str]:
        return list(self._channels.keys())

    def _get_or_raise(self, name: str) -> ManagedChannel:
        channel = self._channels.get(name)
        if channel is None:
            raise KeyError(
                f"Channel '{name}' not registered. Available: {list(self._channels.keys())}"
            )
        return channel
