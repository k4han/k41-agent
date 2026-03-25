import asyncio
import logging
from collections.abc import Awaitable, Callable
from enum import Enum

logger = logging.getLogger(__name__)

ServiceRunner = Callable[[], Awaitable[None]]


class ServiceStatus(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    STARTING = "starting"
    STOPPING = "stopping"
    ERROR = "error"


class ManagedService:
    """Represents a background service managed within the app runtime."""

    def __init__(self, name: str, runner: ServiceRunner):
        self.name = name
        self.runner = runner
        self.status = ServiceStatus.STOPPED
        self.error: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self.status in (ServiceStatus.STARTING, ServiceStatus.RUNNING):
                logger.warning("[%s] Already active with status=%s.", self.name, self.status)
                return

            self.status = ServiceStatus.STARTING
            self.error = None
            self._task = asyncio.create_task(self._run(), name=f"service:{self.name}")
            logger.info("[%s] Starting...", self.name)

    async def stop(self) -> None:
        async with self._lock:
            if self.status in (ServiceStatus.STOPPED, ServiceStatus.STOPPING):
                logger.warning("[%s] Already inactive with status=%s.", self.name, self.status)
                return

            self.status = ServiceStatus.STOPPING
            task = self._task
            logger.info("[%s] Stopping...", self.name)

        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        async with self._lock:
            self.status = ServiceStatus.STOPPED
            self._task = None
            logger.info("[%s] Stopped.", self.name)

    async def _run(self) -> None:
        async with self._lock:
            if self.status == ServiceStatus.STARTING:
                self.status = ServiceStatus.RUNNING
                logger.info("[%s] Running.", self.name)

        try:
            await self.runner()
        except asyncio.CancelledError:
            logger.info("[%s] Cancelled.", self.name)
            raise
        except Exception as exc:
            async with self._lock:
                self.error = str(exc)
                self.status = ServiceStatus.ERROR
            logger.exception("[%s] Error while running service.", self.name)
        finally:
            async with self._lock:
                self._task = None
                if self.status not in (ServiceStatus.STOPPING, ServiceStatus.ERROR):
                    self.status = ServiceStatus.STOPPED

    def info(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "status": self.status,
            "error": self.error,
        }


class ServiceManager:
    """Manages background services owned by a specific app runtime."""

    def __init__(self) -> None:
        self._services: dict[str, ManagedService] = {}

    def register(self, name: str, runner: ServiceRunner) -> None:
        if name in self._services:
            raise ValueError(f"Service '{name}' is already registered.")
        self._services[name] = ManagedService(name, runner)
        logger.info("[ServiceManager] Registered: %s", name)

    async def start(self, name: str) -> None:
        await self._get_or_raise(name).start()

    async def stop(self, name: str) -> None:
        await self._get_or_raise(name).stop()

    async def start_many(self, names: list[str] | tuple[str, ...]) -> None:
        await asyncio.gather(*(self.start(name) for name in names))

    async def start_all(self) -> None:
        await asyncio.gather(*(service.start() for service in self._services.values()))

    async def stop_all(self) -> None:
        for service in reversed(self._services.values()):
            await service.stop()

    def status_all(self) -> list[dict[str, str | None]]:
        return [service.info() for service in self._services.values()]

    def status(self, name: str) -> dict[str, str | None]:
        return self._get_or_raise(name).info()

    def names(self) -> list[str]:
        return list(self._services.keys())

    def _get_or_raise(self, name: str) -> ManagedService:
        service = self._services.get(name)
        if service is None:
            raise KeyError(
                f"Service '{name}' not registered. Available: {list(self._services.keys())}"
            )
        return service
