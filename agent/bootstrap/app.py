import asyncio
import logging
import os
import selectors
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from agent.bootstrap.runtime import AppRuntime
from agent.bootstrap.settings import BootstrapConfig, load_bootstrap_config
from agent.delivery.http import api_router, dashboard_router
from agent.modules.channels.public import list_channel_statuses
from agent.modules.settings.public import create_runtime_settings_service

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app(bootstrap_config: BootstrapConfig | None = None) -> FastAPI:
    bootstrap_config = bootstrap_config or load_bootstrap_config()
    runtime_settings_service = create_runtime_settings_service()
    runtime_settings = runtime_settings_service.get_runtime_settings()
    runtime = AppRuntime(bootstrap_config, runtime_settings)

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI):
        await runtime.startup()
        try:
            yield
        finally:
            await runtime.shutdown()

    fastapi_app = FastAPI(
        title="LangGraph Multi-Platform Agent",
        description="AI agent runtime with optional web, dashboard, and managed channels.",
        version="2.0.0",
        lifespan=lifespan,
    )

    fastapi_app.state.runtime = runtime
    fastapi_app.state.channel_manager = runtime.channel_manager
    fastapi_app.state.bootstrap_config = bootstrap_config
    fastapi_app.state.runtime_settings = runtime_settings
    fastapi_app.state.settings_service = runtime_settings_service

    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if bootstrap_config.enable_api:
        fastapi_app.include_router(api_router)

    if bootstrap_config.enable_dashboard:
        fastapi_app.include_router(dashboard_router)

    @fastapi_app.get("/health")
    async def health(request: Request):
        current_settings: BootstrapConfig = request.app.state.bootstrap_config
        channel_manager = request.app.state.channel_manager
        return {
            "status": "ok",
            "web": current_settings.enable_web,
            "features": {
                "api": current_settings.enable_api,
                "dashboard": current_settings.enable_dashboard,
            },
            "services": list_channel_statuses(channel_manager),
        }

    return fastapi_app


settings = load_bootstrap_config()
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

    logger.info("Web host disabled. Running managed channels only.")
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


__all__ = ["app", "create_app", "main", "run", "settings"]
