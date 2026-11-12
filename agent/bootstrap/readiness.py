"""Check local runtime resources without contacting external AI providers."""

import asyncio

from sqlalchemy import text


async def persistence_is_ready(container) -> bool:
    if not container._persistence_ready or container._checkpointer is None:
        return False
    try:
        async with asyncio.timeout(2):
            async with container.async_engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
