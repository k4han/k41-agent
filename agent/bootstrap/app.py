import asyncio
import logging
import os
import selectors
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from agent.shared.infrastructure.http_logging import HTTPLoggingMiddleware
from agent.shared.infrastructure.csrf_protection import CSRFProtectionMiddleware
from agent.shared.infrastructure.http_errors import register_http_exception_handlers

from agent.bootstrap.runtime import AppRuntime
from agent.bootstrap.settings import BootstrapConfig, load_bootstrap_config
from agent.delivery.http import (
    api_router,
    dashboard_router,
    github_webhook_router,
    telegram_webhook_router,
)
from agent.delivery.http.dashboard.auth_router import router as auth_router
from agent.delivery.http.dashboard.spa import STATIC_DIR, CachedStaticFiles
from agent.modules.channels import list_channel_statuses
from agent.modules.github import get_github_automation_service
from agent.shared.config import get_config_service

log_level = "INFO"
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

SHUTDOWN_SIGNAL = Path.home() / ".k41-agent" / "shutdown.signal"


def create_app(bootstrap_config: BootstrapConfig | None = None) -> FastAPI:
    bootstrap_config = bootstrap_config or load_bootstrap_config()
    config_service = get_config_service()
    runtime_settings = config_service.get_runtime_settings()
    runtime = AppRuntime(bootstrap_config, runtime_settings)

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI):
        await runtime.startup()
        fastapi_app.state.runtime_settings = runtime.runtime_settings
        try:
            yield
        finally:
            await runtime.shutdown()

    fastapi_app = FastAPI(
        title="Kaka Agent API",
        description=(
            "AI agent runtime with multi-platform chat, GitHub automation, "
            "MCP server integration, and a full management dashboard."
        ),
        version="2.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_tags=[
            {"name": "agent", "description": "Chat, streaming, and core agent API endpoints."},
            {"name": "mcp", "description": "MCP server marketplace: search, install, and manage servers."},
            {"name": "github", "description": "GitHub webhook receiver."},
            {"name": "telegram", "description": "Telegram webhook receiver."},
            {"name": "auth", "description": "Admin login, logout, and password management."},
            {"name": "dashboard", "description": "Dashboard overview, sessions, and channel management."},
        ],
    )

    fastapi_app.state.runtime = runtime
    fastapi_app.state.channel_manager = runtime.channel_manager
    fastapi_app.state.bootstrap_config = bootstrap_config
    fastapi_app.state.runtime_settings = runtime_settings
    fastapi_app.state.config_service = config_service
    fastapi_app.state.github_automation_service = get_github_automation_service()
    fastapi_app.state.started_at = time.time()

    register_http_exception_handlers(fastapi_app)

    # CSRF protection middleware - protects against cross-site request forgery
    if bootstrap_config.csrf_protection_enabled:
        logger.info("CSRF protection is enabled")
        fastapi_app.add_middleware(
            CSRFProtectionMiddleware,
            enabled=True,
            exempt_paths={
                "/health",
                "/docs",
                "/redoc",
                "/openapi.json",
                "/login",  # Login page needs to be accessible without CSRF token
                "/github/webhook",  # Webhooks use signature verification instead
                "/telegram/webhook",
            },
        )
    else:
        logger.warning("CSRF protection is disabled - not recommended for production")

    fastapi_app.add_middleware(HTTPLoggingMiddleware)

    # CORS configuration - use whitelist from config instead of allowing all origins
    cors_origins = bootstrap_config.cors_origins
    logger.info("CORS allowed origins: %s", cors_origins)
    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    if bootstrap_config.enable_api:
        fastapi_app.include_router(api_router)
        # Backward compatibility: redirect /api/* to /v1/api/*
        @fastapi_app.middleware("http")
        async def redirect_legacy_api(request: Request, call_next):
            if request.url.path.startswith("/api/") and not request.url.path.startswith("/v1/api/"):
                # Redirect to versioned endpoint
                new_path = f"/v1{request.url.path}"
                logger.warning(
                    "Legacy API endpoint accessed: %s - please update to: %s",
                    request.url.path,
                    new_path,
                )
                from fastapi.responses import RedirectResponse
                return RedirectResponse(url=new_path, status_code=307)
            return await call_next(request)

    fastapi_app.include_router(telegram_webhook_router)
    fastapi_app.include_router(github_webhook_router)

    if bootstrap_config.enable_dashboard:
        fastapi_app.include_router(auth_router)
        fastapi_app.include_router(dashboard_router)
        dashboard_assets_app = CachedStaticFiles(directory=STATIC_DIR, check_dir=False)
        fastapi_app.mount(
            "/dashboard-assets",
            GZipMiddleware(dashboard_assets_app, minimum_size=500),
            name="dashboard-assets",
        )

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

        async def monitor_shutdown_signal():
            while True:
                if SHUTDOWN_SIGNAL.exists():
                    logger.info("Shutdown signal received. Stopping server...")
                    SHUTDOWN_SIGNAL.unlink(missing_ok=True)
                    server.should_exit = True
                    return
                await asyncio.sleep(0.5)

        shutdown_task = asyncio.create_task(monitor_shutdown_signal())
        logger.info("Starting web host on %s:%s", settings.host, settings.port)
        await server.serve()
        shutdown_task.cancel()
        return

    logger.info("Web host disabled. Running managed channels only.")
    runtime: AppRuntime = app.state.runtime
    await runtime.startup()
    try:
        while True:
            if SHUTDOWN_SIGNAL.exists():
                logger.info("Shutdown signal received. Stopping server...")
                SHUTDOWN_SIGNAL.unlink(missing_ok=True)
                break
            await asyncio.sleep(0.5)
    finally:
        await runtime.shutdown()


def run() -> None:
    try:
        if os.name == "nt":
            asyncio.run(
                main(),
                loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
            )
            return

        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown requested by user.")


__all__ = ["app", "create_app", "main", "run", "settings"]
