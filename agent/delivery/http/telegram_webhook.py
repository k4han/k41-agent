from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response

from agent.modules.channels import get_telegram_webhook_runtime
from agent.shared.config import get_config_service

router = APIRouter(tags=["telegram"])
logger = logging.getLogger(__name__)

TELEGRAM_WEBHOOK_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def _expected_webhook_secret() -> str:
    runtime = get_telegram_webhook_runtime()
    if runtime is not None:
        return runtime.secret

    config = get_config_service()
    return config.get_str("channels.telegram.webhook_secret", "")


@router.post("/channels/telegram/webhook")
async def telegram_webhook(
    request: Request,
    secret_token: str | None = Header(
        default=None,
        alias=TELEGRAM_WEBHOOK_SECRET_HEADER,
    ),
) -> Response:
    expected_secret = _expected_webhook_secret()
    if expected_secret and secret_token != expected_secret:
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret.")

    runtime = get_telegram_webhook_runtime()
    if runtime is None:
        raise HTTPException(status_code=503, detail="Telegram webhook runtime is not active.")

    try:
        payload: dict[str, Any] = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid Telegram update payload.") from exc

    try:
        from aiogram.types import Update

        update = Update.model_validate(payload, context={"bot": runtime.bot})
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid Telegram update payload.") from exc

    try:
        await runtime.dispatcher.feed_update(runtime.bot, update)
    except Exception as exc:
        logger.exception("Telegram webhook dispatcher failed.")
        raise HTTPException(status_code=500, detail="Telegram dispatcher failed.") from exc

    return Response(status_code=204)
