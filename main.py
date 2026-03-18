# main.py
# Chạy FastAPI + Telegram + Discord trong cùng 1 process, 1 event loop.
# FastAPI đóng vai trò API server + dashboard bật/tắt bots.

import os
import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.graphs               import setup_all_graphs
from agent.services.bot_manager import BotManager
from agent.adapters.fastapi     import router as api_router, dashboard_router

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── Đăng ký các bot vào BotManager ─────────────────────────────────────────

def setup_bots() -> None:
    """
    Đăng ký Telegram và Discord vào BotManager.
    Chỉ đăng ký nếu token tồn tại trong .env.
    """
    manager = BotManager.get()

    # Telegram
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        from agent.adapters.telegram.handler import run_telegram_bot
        manager.register("telegram", run_telegram_bot)
        logger.info("Registered: telegram")
    else:
        logger.warning("TELEGRAM_BOT_TOKEN not set — telegram bot skipped.")

    # Discord
    if os.getenv("DISCORD_BOT_TOKEN"):
        from agent.adapters.discord.handler import run_discord_bot
        manager.register("discord", run_discord_bot)
        logger.info("Registered: discord")
    else:
        logger.warning("DISCORD_BOT_TOKEN not set — discord bot skipped.")


# ── FastAPI app ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Building graphs...")
    setup_all_graphs()

    logger.info("Setting up bots...")
    setup_bots()

    # Auto-start bots nếu AUTO_START_BOTS=true trong .env
    if os.getenv("AUTO_START_BOTS", "true").lower() == "true":
        logger.info("Auto-starting bots...")
        await BotManager.get().start_all()

    logger.info("App ready.")
    yield

    # Shutdown
    logger.info("Stopping all bots...")
    await BotManager.get().stop_all()
    logger.info("App shutdown.")


app = FastAPI(
    title="LangGraph Multi-Platform Agent",
    description="AI Agent chạy đồng thời trên FastAPI, Telegram, Discord.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(dashboard_router)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "bots":   BotManager.get().status_all(),
    }


# ── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Chạy uvicorn programmatically trong asyncio
    # → cùng event loop với Telegram/Discord bots
    config = uvicorn.Config(
        app="main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=False,          # reload=True không tương thích với async bots
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    asyncio.run(server.serve())

