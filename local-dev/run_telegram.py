# run_telegram.py
# Chạy Telegram bot standalone (không cần FastAPI)

import asyncio
import logging
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)

from agent.graphs import setup_all_graphs
from agent.adapters.telegram.handler import run_telegram_bot

if __name__ == "__main__":
    setup_all_graphs()
    asyncio.run(run_telegram_bot())
