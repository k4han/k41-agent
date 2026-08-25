from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response

from agent.modules.channels import get_channel_webhook_runtime
from agent.shared.config import get_config_service

router = APIRouter(tags=["zalo"])
logger = logging.getLogger(__name__)

ZALO_WEBHOOK_SECRET_HEADER = "X-Bot-Api-Secret-Token"


def _expected_zalo_webhook_secret() -> str:
    runtime = get_channel_webhook_runtime("zalo")
    if runtime is not None:
        # runtime has .secret attribute
        secret = getattr(runtime, "secret", "")
        if isinstance(secret, str):
            return secret
    config = get_config_service()
    return config.get_str("channels.zalo.webhook_secret", "")


@router.post("/channels/zalo/webhook")
async def zalo_webhook(
    request: Request,
    secret_token: str | None = Header(
        default=None,
        alias=ZALO_WEBHOOK_SECRET_HEADER,
    ),
) -> Response:
    expected_secret = _expected_zalo_webhook_secret()
    if expected_secret and secret_token != expected_secret:
        raise HTTPException(status_code=401, detail="Invalid Zalo webhook secret.")

    runtime = get_channel_webhook_runtime("zalo")
    if runtime is None:
        raise HTTPException(status_code=503, detail="Zalo webhook runtime is not active.")

    try:
        payload: dict[str, Any] = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid Zalo update payload.") from exc

    # Zalo wraps payload as {ok: true, result: {event_name, message}}
    inner: dict[str, Any] | None = None
    if isinstance(payload, dict):
        if payload.get("ok") is True and "result" in payload and isinstance(payload["result"], dict):
            inner = payload["result"]
        elif "event_name" in payload and "message" in payload:
            inner = payload
        else:
            # Try to handle direct result dict without ok wrapper
            result = payload.get("result")
            if isinstance(result, dict) and "event_name" in result:
                inner = result
            else:
                inner = payload

    if inner is None or "event_name" not in inner:
        raise HTTPException(status_code=400, detail="Invalid Zalo update payload.")

    try:
        from agent.modules.channels import handle_zalo_update

        await handle_zalo_update(inner)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Zalo webhook handler failed.")
        raise HTTPException(status_code=500, detail="Zalo handler failed.") from exc

    return Response(status_code=204)
