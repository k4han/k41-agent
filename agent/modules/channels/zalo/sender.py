from __future__ import annotations

import base64
import logging
import mimetypes
from typing import Any
from urllib.parse import urlparse

import httpx

from agent.shared.config import get_config_service
from agent.shared.infrastructure.validation import is_placeholder_value

logger = logging.getLogger(__name__)

ZALO_API_BASE = "https://bot-api.zaloplatforms.com"
ZALO_MAX_TEXT_LENGTH = 2000


def _api_url(token: str, method: str) -> str:
    return f"{ZALO_API_BASE}/bot{token}/{method}"


def chunk_zalo_message(text: str, max_len: int = ZALO_MAX_TEXT_LENGTH) -> list[str]:
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        slice_text = remaining[:max_len]
        last_nl = slice_text.rfind("\n")
        if last_nl > max_len * 0.5:
            chunks.append(remaining[:last_nl].rstrip())
            remaining = remaining[last_nl:].lstrip("\n")
            if not remaining:
                break
        else:
            chunks.append(slice_text)
            remaining = remaining[max_len:]
    return chunks


def _resolve_parse_mode(mode: str | None) -> str | None:
    if mode == "markdown":
        return "markdown"
    if mode == "html":
        return "html"
    return None


async def send_zalo_chat_action(
    chat_id: str,
    action: str = "typing",
) -> bool:
    """Send a chat action (e.g. ``typing``) to show the bot is processing.

    Zalo Bot API does not support editing messages, so ``typing`` is the
    recommended way to indicate activity instead of sending an editable
    ``Processing...`` placeholder. See https://bot.zapps.me/docs/apis/sendChatAction/
    """
    config = get_config_service()
    token = config.get_str("channels.zalo.bot_token", "")
    if is_placeholder_value(token):
        logger.debug("Zalo bot token not configured, skipping sendChatAction.")
        return False
    if action not in {"typing", "upload_photo"}:
        action = "typing"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(
                _api_url(token, "sendChatAction"),
                json={"chat_id": str(chat_id), "action": action},
            )
        except httpx.TimeoutException:
            logger.debug("Timed out sending Zalo chat action to %s", chat_id)
            return False
        except httpx.HTTPError as exc:
            logger.debug("Zalo sendChatAction HTTP error: %s", exc)
            return False
        if resp.status_code != 200:
            logger.debug("Zalo sendChatAction failed %s %s", resp.status_code, resp.text)
            return False
        try:
            data = resp.json()
        except ValueError:
            logger.debug("Zalo sendChatAction returned non-JSON: %s", resp.text)
            return False
        if not data.get("ok"):
            logger.debug("Zalo sendChatAction ok false: %s", data)
            return False
        return True


async def download_zalo_image_as_attachment(
    photo_url: str,
    *,
    timeout: float = 15.0,
    max_bytes: int = 5 * 1024 * 1024,
) -> dict[str, Any] | None:
    """Download a Zalo image URL and convert it to an agent attachment dict.

    Zalo sends ``message.photo`` as a URL (``https://...``) for
    ``message.image.received`` events. The agent runtime expects image
    attachments as ``{"kind": "image", "base64": "...", "mime_type": "..."}``.
    Returns ``None`` if download fails or the URL is empty.
    """
    url = (photo_url or "").strip()
    if not url:
        return None
    # Zalo docs show photo as a URL string; if it's not an http URL we cannot fetch.
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        logger.warning("Zalo image photo is not a downloadable URL: %r", url[:120])
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
    except httpx.TimeoutException:
        logger.warning("Timed out downloading Zalo image %s", url[:120])
        return None
    except httpx.HTTPError as exc:
        logger.warning("Failed to download Zalo image %s: %s", url[:120], exc)
        return None
    if resp.status_code != 200:
        logger.warning("Zalo image download failed %s %s for %s", resp.status_code, resp.text[:200], url[:120])
        return None
    content = resp.content
    if not content:
        logger.warning("Zalo image download returned empty body for %s", url[:120])
        return None
    if len(content) > max_bytes:
        logger.warning("Zalo image too large (%s bytes), skipping: %s", len(content), url[:120])
        return None
    # Guess mime type from header or URL
    mime_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not mime_type or not mime_type.startswith("image/"):
        guessed, _ = mimetypes.guess_type(url)
        mime_type = guessed or "image/jpeg"
    # Ensure we use a known image mime; fallback to jpeg
    if mime_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        # Let the runtime handle validation, but normalize common types
        if mime_type == "image/jpg":
            mime_type = "image/jpeg"
        elif not mime_type.startswith("image/"):
            mime_type = "image/jpeg"
    # Derive filename
    filename = "zalo-image"
    try:
        path_part = parsed.path or ""
        if "/" in path_part:
            candidate = path_part.rsplit("/", 1)[-1]
            if "." in candidate and len(candidate) <= 64:
                filename = candidate
            elif candidate:
                filename = candidate
        # Ensure extension matches mime
        ext = mimetypes.guess_extension(mime_type) or ".jpg"
        if "." not in filename:
            filename = f"{filename}{ext}"
        elif not filename.lower().endswith(ext.lower()):
            # Keep original filename but ensure it has an extension
            pass
    except Exception:
        filename = "zalo-image.jpg"
    encoded = base64.b64encode(content).decode("ascii")
    return {
        "name": filename,
        "mime_type": mime_type,
        "size": len(content),
        "kind": "image",
        "base64": encoded,
    }


async def send_zalo_message(
    chat_id: str,
    text: str,
    *,
    mode: str = "markdown",
) -> list[Any]:
    config = get_config_service()
    token = config.get_str("channels.zalo.bot_token", "")
    if is_placeholder_value(token):
        logger.warning("Zalo bot token not configured, skipping send.")
        return []
    parse_mode = _resolve_parse_mode(mode)
    chunks = chunk_zalo_message(text)
    if not chunks:
        return []
    sent: list[Any] = []
    async with httpx.AsyncClient(timeout=10) as client:
        for chunk in chunks:
            payload: dict[str, Any] = {"chat_id": str(chat_id), "text": chunk}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            try:
                resp = await client.post(_api_url(token, "sendMessage"), json=payload)
            except httpx.TimeoutException:
                logger.warning("Timed out sending Zalo message to %s", chat_id)
                continue
            except httpx.HTTPError as exc:
                logger.warning("Zalo sendMessage HTTP error: %s", exc)
                continue
            if resp.status_code != 200:
                logger.warning("Zalo sendMessage failed %s %s", resp.status_code, resp.text)
                continue
            try:
                data = resp.json()
            except ValueError:
                logger.warning("Zalo sendMessage returned non-JSON: %s", resp.text)
                continue
            if not data.get("ok"):
                logger.warning("Zalo sendMessage ok false: %s", data)
                continue
            sent.append(data.get("result"))
    return sent
