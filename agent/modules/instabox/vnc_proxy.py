"""Relay a dashboard WebSocket to an Instabox machine's VNC WebSocket."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

UPSTREAM_OPEN_TIMEOUT = 15.0


async def relay_vnc(websocket: Any, service: Any, machine_id: str, closer: asyncio.Event) -> None:
    """Pump RFB frames both ways until either side leaves or the machine is invalidated.

    The browser never receives Instabox credentials; the server authenticates
    upstream with its own API key.
    """
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    client = await service.client()
    try:
        upstream_cm = connect(
            client.vnc_url(machine_id),
            additional_headers=client.vnc_headers(),
            max_size=None,
            open_timeout=UPSTREAM_OPEN_TIMEOUT,
            ping_interval=20,
        )
        upstream = await upstream_cm.__aenter__()
    except InvalidStatus as exc:
        status = getattr(getattr(exc, "response", None), "status_code", 0)
        code = {409: "desktop_not_running", 429: "quota_exceeded"}.get(status, "upstream_rejected")
        await websocket.send_json({"type": "error", "code": code, "message": "The desktop connection was refused."})
        return
    try:
        await websocket.send_json({"type": "ready"})

        async def downstream() -> None:
            async for message in upstream:
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)

        async def upstream_pump() -> None:
            while True:
                event = await websocket.receive()
                if event.get("type") == "websocket.disconnect":
                    return
                if event.get("bytes") is not None:
                    await upstream.send(event["bytes"])
                elif event.get("text") is not None:
                    await upstream.send(event["text"])

        tasks = [
            asyncio.create_task(downstream()),
            asyncio.create_task(upstream_pump()),
            asyncio.create_task(closer.wait()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        # Viewers count toward the per-user Instabox connection limit; close upstream promptly.
        with contextlib.suppress(Exception):
            await upstream_cm.__aexit__(None, None, None)


__all__ = ["relay_vnc"]
