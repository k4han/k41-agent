import asyncio
import logging
import os
import selectors
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from agent.adapters.fastapi import dashboard_router, router as api_router
from agent.graphs import setup_all_graphs
from agent.persistence import close_persistence, initialize_persistence
from agent.services import ServiceManager
from agent.services.service_manager import ServiceRunner

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def parse_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class AppSettings:
    host: str
    port: int
    enable_web: bool
    enable_api: bool
    enable_dashboard: bool
    service_boot_flags: dict[str, bool]

    @classmethod
    def from_env(cls) -> "AppSettings":
        return cls(
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", 8000)),
            enable_web=parse_bool_env("ENABLE_WEB", True),
            enable_api=parse_bool_env("ENABLE_API", True),
            enable_dashboard=parse_bool_env("ENABLE_DASHBOARD", True),
            service_boot_flags={
                "telegram": parse_bool_env("ENABLE_TELEGRAM", True),
                "discord": parse_bool_env("ENABLE_DISCORD", True),
            },
        )


@dataclass(frozen=True, slots=True)
class ServiceSpec:
    name: str
    runner_loader: Callable[[], ServiceRunner]
    required_env: tuple[str, ...] = ()


def load_telegram_runner() -> ServiceRunner:
    from agent.adapters.telegram.handler import run_telegram_bot

    return run_telegram_bot


def load_discord_runner() -> ServiceRunner:
    from agent.adapters.discord.handler import run_discord_bot

    return run_discord_bot


SERVICE_SPECS = (
    ServiceSpec(
        name="telegram",
        runner_loader=load_telegram_runner,
        required_env=("TELEGRAM_BOT_TOKEN",),
    ),
    ServiceSpec(
        name="discord",
        runner_loader=load_discord_runner,
        required_env=("DISCORD_BOT_TOKEN",),
    ),
)


class AppRuntime:
    """Owns shared resources and managed background services."""

    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.service_manager = ServiceManager()
        self._services_registered = False
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

            logger.info("Building graphs...")
            setup_all_graphs()

            self._register_services()
            await self._start_enabled_services()
            self._started = True
            logger.info("Application runtime is ready.")
        except Exception:
            logger.exception("Application startup failed.")
            await self.shutdown()
            raise

    async def shutdown(self) -> None:
        if self.service_manager.names():
            logger.info("Stopping managed services...")
            await self.service_manager.stop_all()

        if self._persistence_ready:
            logger.info("Closing persistence...")
            await close_persistence()
            self._persistence_ready = False

        self._started = False
        logger.info("Application runtime stopped.")

    def _register_services(self) -> None:
        if self._services_registered:
            return

        logger.info("Registering configured services...")
        for spec in SERVICE_SPECS:
            self.service_manager.register(spec.name, spec.runner_loader())
            logger.info("Service registered: %s", spec.name)

        self._services_registered = True

    async def _start_enabled_services(self) -> None:
        services_to_start: list[str] = []
        for spec in SERVICE_SPECS:
            if not self.settings.service_boot_flags.get(spec.name, True):
                logger.info("Service starts disabled by config: %s", spec.name)
                continue

            missing_env = [env_name for env_name in spec.required_env if not os.getenv(env_name)]
            if missing_env:
                logger.warning(
                    "Service '%s' is configured to start but required env vars are missing: %s",
                    spec.name,
                    ", ".join(missing_env),
                )
                continue

            services_to_start.append(spec.name)

        if not services_to_start:
            logger.info("No background services configured to start on boot.")
            return

        logger.info(
            "Starting configured background services: %s",
            ", ".join(services_to_start),
        )
        await self.service_manager.start_many(services_to_start)


def create_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings or AppSettings.from_env()
    runtime = AppRuntime(settings)

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI):
        await runtime.startup()
        try:
            yield
        finally:
            await runtime.shutdown()

    fastapi_app = FastAPI(
        title="LangGraph Multi-Platform Agent",
        description="AI agent runtime with optional web, dashboard, and managed services.",
        version="2.0.0",
        lifespan=lifespan,
    )

    fastapi_app.state.runtime = runtime
    fastapi_app.state.service_manager = runtime.service_manager
    fastapi_app.state.settings = settings

    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if settings.enable_api:
        fastapi_app.include_router(api_router)

    if settings.enable_dashboard:
        fastapi_app.include_router(dashboard_router)

    @fastapi_app.get("/health")
    async def health(request: Request):
        current_settings: AppSettings = request.app.state.settings
        service_manager: ServiceManager = request.app.state.service_manager
        return {
            "status": "ok",
            "web": current_settings.enable_web,
            "features": {
                "api": current_settings.enable_api,
                "dashboard": current_settings.enable_dashboard,
            },
            "services": service_manager.status_all(),
        }

    return fastapi_app


settings = AppSettings.from_env()
app = create_app(settings)


async def main() -> None:
    if settings.enable_web:
        config = uvicorn.Config(
            app=app,
            host=settings.host,
            port=settings.port,
            reload=False,
            loop="asyncio",
        )
        server = uvicorn.Server(config)
        logger.info("Starting web host on %s:%s", settings.host, settings.port)
        await server.serve()
        return

    logger.info("Web host disabled. Running managed services only.")
    runtime: AppRuntime = app.state.runtime
    await runtime.startup()
    try:
        await asyncio.Event().wait()
    finally:
        await runtime.shutdown()


def run() -> None:
    if os.name == "nt":
        asyncio.run(
            main(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
        return

    asyncio.run(main())


if __name__ == "__main__":
    run()
