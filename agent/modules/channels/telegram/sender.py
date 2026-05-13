import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from agent.modules.channels.telegram.formatter import (
    chunk_telegram_message,
    format_telegram_message,
)

logger = logging.getLogger(__name__)

TELEGRAM_HTML_PARSE_MODE = "HTML"

TelegramTextMode = Literal["markdown", "html", "plain"]
TelegramSendCallable = Callable[[str, str | None], Awaitable[Any]]


def strip_telegram_html(text: str) -> str:
    """Remove a small Telegram HTML subset for plain-text fallback sends."""
    text = re.sub(r"</?(?:b|strong|i|em|u|ins|s|strike|del|code|pre)>", "", text)
    text = re.sub(r'<a\s+href="[^"]*">([^<]*)</a>', r"\1", text)
    text = re.sub(r"</?blockquote[^>]*>", "", text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    text = text.replace("&quot;", '"').replace("&#x27;", "'")
    return text


def prepare_telegram_chunks(
    text: str,
    *,
    mode: TelegramTextMode = "markdown",
) -> tuple[list[str], str | None]:
    """Format and chunk text for Telegram."""
    if mode == "plain":
        return chunk_telegram_message(text), None

    if mode == "html":
        return chunk_telegram_message(text), TELEGRAM_HTML_PARSE_MODE

    try:
        html_text = format_telegram_message(text)
    except Exception as exc:
        logger.error("Failed to format Telegram message: %s", exc)
        return chunk_telegram_message(text), None

    return chunk_telegram_message(html_text), TELEGRAM_HTML_PARSE_MODE


async def _send_or_fallback(
    send_text: TelegramSendCallable,
    chunk: str,
    parse_mode: str | None,
) -> Any | None:
    try:
        return await send_text(chunk, parse_mode)
    except Exception as exc:
        if parse_mode is None:
            logger.error("Failed to send Telegram text chunk: %s", exc)
            return None

        logger.warning("Failed to send Telegram HTML chunk, retrying as plain text: %s", exc)
        try:
            return await send_text(strip_telegram_html(chunk), None)
        except Exception as fallback_exc:
            logger.error("Failed to send Telegram fallback chunk: %s", fallback_exc)
            return None


async def send_telegram_chunks(
    send_text: TelegramSendCallable,
    text: str,
    *,
    mode: TelegramTextMode = "markdown",
) -> list[Any]:
    """Send formatted text through the provided Telegram send function."""
    chunks, parse_mode = prepare_telegram_chunks(text, mode=mode)
    sent: list[Any] = []
    for chunk in chunks:
        result = await _send_or_fallback(send_text, chunk, parse_mode)
        if result is not None:
            sent.append(result)
    return sent


async def answer_telegram_message(
    message: Any,
    text: str,
    *,
    mode: TelegramTextMode = "markdown",
) -> list[Any]:
    async def send_text(chunk: str, parse_mode: str | None) -> Any:
        return await message.answer(chunk, parse_mode=parse_mode)

    return await send_telegram_chunks(send_text, text, mode=mode)


async def send_telegram_bot_message(
    bot: Any,
    chat_id: str,
    text: str,
    *,
    mode: TelegramTextMode = "html",
) -> list[Any]:
    async def send_text(chunk: str, parse_mode: str | None) -> Any:
        return await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=parse_mode)

    return await send_telegram_chunks(send_text, text, mode=mode)


async def send_telegram_response(
    message: Any,
    first_message: Any,
    text: str,
    *,
    mode: TelegramTextMode = "markdown",
) -> list[Any]:
    """Edit the first Telegram response chunk and send overflow chunks as replies."""
    chunks, parse_mode = prepare_telegram_chunks(text, mode=mode)
    sent: list[Any] = []

    async def answer_text(chunk: str, chunk_parse_mode: str | None) -> Any:
        return await message.answer(chunk, parse_mode=chunk_parse_mode)

    async def edit_text(chunk: str, chunk_parse_mode: str | None) -> Any:
        return await first_message.edit_text(chunk, parse_mode=chunk_parse_mode)

    for index, chunk in enumerate(chunks):
        send_text = edit_text if index == 0 else answer_text
        result = await _send_or_fallback(send_text, chunk, parse_mode)
        if result is not None:
            sent.append(result)
    return sent
