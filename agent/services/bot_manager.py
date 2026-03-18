# agent/services/bot_manager.py

import asyncio
import logging
from enum import Enum
from typing import Callable, Coroutine

logger = logging.getLogger(__name__)


class BotStatus(str, Enum):
    STOPPED  = "stopped"
    RUNNING  = "running"
    STARTING = "starting"
    STOPPING = "stopping"
    ERROR    = "error"


class BotService:
    """Represents a bot service (Telegram, Discord,...)."""

    def __init__(self, name: str, runner: Callable[[], Coroutine]):
        self.name    = name
        self.runner  = runner          # async function to run the bot
        self.status  = BotStatus.STOPPED
        self.error   = None
        self._task: asyncio.Task | None = None

    async def start(self):
        if self.status == BotStatus.RUNNING:
            logger.warning(f"[{self.name}] Already running.")
            return

        self.status = BotStatus.STARTING
        self.error  = None
        logger.info(f"[{self.name}] Starting...")

        self._task = asyncio.create_task(self._run(), name=self.name)

    async def stop(self):
        if self.status != BotStatus.RUNNING:
            logger.warning(f"[{self.name}] Not running.")
            return

        self.status = BotStatus.STOPPING
        logger.info(f"[{self.name}] Stopping...")

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        self.status = BotStatus.STOPPED
        self._task  = None
        logger.info(f"[{self.name}] Stopped.")

    async def _run(self):
        try:
            self.status = BotStatus.RUNNING
            logger.info(f"[{self.name}] Running.")
            await self.runner()
        except asyncio.CancelledError:
            logger.info(f"[{self.name}] Cancelled.")
        except Exception as e:
            self.error  = str(e)
            self.status = BotStatus.ERROR
            logger.error(f"[{self.name}] Error: {e}")
        finally:
            if self.status not in (BotStatus.STOPPING, BotStatus.ERROR):
                self.status = BotStatus.STOPPED

    def info(self) -> dict:
        return {
            "name":   self.name,
            "status": self.status,
            "error":  self.error,
        }


class BotManager:
    """
    Manages all bot services.
    Singleton — shared across the app.
    """
    _instance: "BotManager | None" = None
    _services: dict[str, BotService] = {}

    @classmethod
    def get(cls) -> "BotManager":
        if cls._instance is None:
            cls._instance = BotManager()
        return cls._instance

    def register(self, name: str, runner: Callable[[], Coroutine]) -> None:
        self._services[name] = BotService(name, runner)
        logger.info(f"[BotManager] Registered: {name}")

    async def start(self, name: str) -> None:
        self._get_or_raise(name)
        await self._services[name].start()

    async def stop(self, name: str) -> None:
        self._get_or_raise(name)
        await self._services[name].stop()

    async def start_all(self) -> None:
        for svc in self._services.values():
            await svc.start()

    async def stop_all(self) -> None:
        for svc in self._services.values():
            await svc.stop()

    def status_all(self) -> list[dict]:
        return [svc.info() for svc in self._services.values()]

    def status(self, name: str) -> dict:
        return self._get_or_raise(name).info()

    def _get_or_raise(self, name: str) -> BotService:
        if name not in self._services:
            raise KeyError(f"Service '{name}' not registered. "
                           f"Available: {list(self._services.keys())}")
        return self._services[name]
