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
from agent.bootstrap.settings import AppSettings
from agent.delivery.http import api_router, dashboard_router
from agent.modules.channels.public import list_channel_statuses
from agent.modules.settings.public import get_service as get_settings_service

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


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
        description="AI agent runtime with optional web, dashboard, and managed channels.",
        version="2.0.0",
        lifespan=lifespan,
    )

    fastapi_app.state.runtime = runtime
    fastapi_app.state.channel_manager = runtime.channel_manager
    fastapi_app.state.settings = settings
    fastapi_app.state.settings_service = get_settings_service()

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
