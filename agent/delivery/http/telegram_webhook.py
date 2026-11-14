from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response

from agent.modules.channels import get_channel_webhook_runtime
from agent.shared.config import get_config_service

if TYPE_CHECKING:
    from aiogram.types import Update

    from agent.modules.channels.telegram.bot import TelegramWebhookRuntime

router = APIRouter(tags=["telegram"])
logger = logging.getLogger(__name__)

TELEGRAM_WEBHOOK_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def _expected_webhook_secret() -> str:
    runtime = get_channel_webhook_runtime("telegram")
    if runtime is not None:
        return runtime.secret

    config = get_config_service()
    return config.get_str("channels.telegram.webhook_secret", "")


@router.post("/channels/telegram/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    secret_token: str | None = Header(
        default=None,
        alias=TELEGRAM_WEBHOOK_SECRET_HEADER,
    ),
) -> Response:
    expected_secret = _expected_webhook_secret()
    if expected_secret and secret_token != expected_secret:
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret.")

    runtime = get_channel_webhook_runtime("telegram")
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

    background_tasks.add_task(_process_telegram_webhook, runtime=runtime, update=update)
    return Response(status_code=200)


async def _process_telegram_webhook(*, runtime: TelegramWebhookRuntime, update: Update) -> None:
    try:
        await runtime.dispatcher.feed_update(runtime.bot, update)
    except Exception:
        logger.exception("Telegram webhook processing failed: update_id=%s", update.update_id)
