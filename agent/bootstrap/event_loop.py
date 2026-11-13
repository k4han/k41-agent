"""Select an entry point loop while respecting database driver requirements."""

import asyncio
import logging
import sys
from collections.abc import Callable


logger = logging.getLogger(__name__)


def get_event_loop_factory(database_type: str) -> Callable[[], asyncio.AbstractEventLoop] | None:
    if sys.platform == "win32" and database_type == "postgres":
        # Psycopg's async checkpointer rejects ProactorEventLoop on Windows.
        logger.warning(
            "PostgreSQL requires SelectorEventLoop on Windows; its 512-socket "
            "limit still applies. Use SQLite or a non-Windows host for higher concurrency."
        )
        return asyncio.SelectorEventLoop
    return None
