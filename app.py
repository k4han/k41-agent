import os
import asyncio
import logging
import selectors
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.graphs               import setup_all_graphs
from agent.persistence          import close_persistence, initialize_persistence
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


async def main():
    # Initialize persistence and graphs
    await initialize_persistence()
    setup_all_graphs()

    # Setup bots
    setup_bots()

    # Auto-start bots nếu AUTO_START_BOTS=true trong .env
    if os.getenv("AUTO_START_BOTS", "true").lower() == "true":
        await BotManager.get().start_all()

    await asyncio.Event().wait()  # Giữ app chạy


async def shutdown_graceful():
    """Graceful shutdown: dừng bots trước, sau đó close DB"""
    logger.info("Stopping all bots...")
    await BotManager.get().stop_all()

    logger.info("Closing persistence...")
    await close_persistence()

    logger.info("Shutdown complete.")

if __name__ == "__main__":
    try:
        if os.name == "nt":
            asyncio.run(
                main(),
                loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
            )
        else:
            asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        if os.name == "nt":
            asyncio.run(
                shutdown_graceful(),
                loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
            )
        else:
            asyncio.run(shutdown_graceful())