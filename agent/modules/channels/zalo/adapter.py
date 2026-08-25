from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from agent.modules.channels.commands import CommandSpec
from agent.modules.channels.contracts import (
    ChannelSettingField,
    ChannelSettingSection,
    InboundMessage,
    OutboundMessage,
)
from agent.modules.channels.pipeline import process_inbound_message
from agent.modules.channels.service_specs import ZALO_SETTINGS_SCHEMA, ZALO_SETTINGS_SECTIONS
from agent.modules.channels.zalo.sender import (
    download_zalo_image_as_attachment,
    send_zalo_chat_action,
    send_zalo_message,
)

logger = logging.getLogger(__name__)


class ZaloChannelAdapter:
    name = "zalo"
    title = "Zalo"
    summary = "Chat with your agents from Zalo."
    tagline = "Bot platform"
    capabilities = frozenset({"chat", "outbound", "streaming"})
    settings_sections = ZALO_SETTINGS_SECTIONS
    settings_schema = ZALO_SETTINGS_SCHEMA

    def __init__(self) -> None:
        self._client: Any | None = None

    def create_runner(self):
        from agent.modules.channels.zalo.bot import run_zalo_bot

        return run_zalo_bot

    def set_client(self, client: Any | None) -> None:
        self._client = client

    async def send(self, destination: str, message: OutboundMessage) -> bool:
        try:
            sent = await send_zalo_message(destination, message.text, mode=message.mode)
            return bool(sent)
        except Exception as exc:
            logger.warning("Failed to send Zalo outbound message: %s", exc)
            return False

    async def test_connection(self):
        from agent.modules.channels.diagnostics import test_zalo_connection

        return await test_zalo_connection()

    async def sync_commands(self, commands: Sequence[CommandSpec]) -> None:
        return None


_adapter = ZaloChannelAdapter()


def get_zalo_adapter() -> ZaloChannelAdapter:
    return _adapter


def _is_private_chat(chat: dict[str, Any]) -> bool:
    chat_type = str(chat.get("chat_type") or "").upper()
    if not chat_type:
        return True
    return chat_type == "PRIVATE"


async def handle_zalo_update(payload: dict[str, Any]) -> None:
    """Convert Zalo webhook/polling payload into InboundMessage.

    Expected payload shape (from docs):
    {
        "event_name": "message.text.received",
        "message": {
            "from": {"id": "...", "display_name": "...", "is_bot": false},
            "chat": {"id": "...", "chat_type": "PRIVATE"},
            "text": "hello",
            "message_id": "...",
            "date": 1234567890,
            "caption": "..."  # for image
            "photo": "...",
        }
    }
    """
    if not isinstance(payload, dict):
        return
    event_name = str(payload.get("event_name") or "")
    message = payload.get("message")
    if not isinstance(message, dict):
        return

    # Extract text: text for text messages, caption for image messages
    text = str(message.get("text") or message.get("caption") or "").strip()
    # Zalo sends image messages as ``message.image.received`` with ``photo`` URL
    # and optional ``caption``. We support images with or without caption by
    # downloading the photo and forwarding it as a vision attachment.
    photo_url = ""
    raw_photo = message.get("photo")
    if isinstance(raw_photo, str):
        photo_url = raw_photo.strip()
    elif isinstance(raw_photo, dict):
        # Some Zalo payloads may send photo as {url: "..."} or {photo: "..."}
        for key in ("url", "photo", "href", "src"):
            val = raw_photo.get(key)
            if isinstance(val, str) and val.strip():
                photo_url = val.strip()
                break
        if not photo_url:
            # Fallback: first string value that looks like a URL
            for val in raw_photo.values():
                if isinstance(val, str) and val.strip().startswith("http"):
                    photo_url = val.strip()
                    break
    elif isinstance(raw_photo, list) and raw_photo:
        first = raw_photo[0]
        if isinstance(first, str):
            photo_url = first.strip()
        elif isinstance(first, dict):
            photo_url = str(first.get("url") or first.get("photo") or "").strip()
    # Fallback for alternative field names some Zalo versions may use
    if not photo_url:
        for alt_key in ("photo_url", "image", "image_url", "url"):
            alt_val = message.get(alt_key)
            if isinstance(alt_val, str) and alt_val.strip():
                photo_url = alt_val.strip()
                break
            if isinstance(alt_val, dict):
                for key in ("url", "photo"):
                    val = alt_val.get(key)
                    if isinstance(val, str) and val.strip():
                        photo_url = val.strip()
                        break
                if photo_url:
                    break

    attachments: list[dict[str, Any]] | None = None
    if photo_url:
        try:
            attachment = await download_zalo_image_as_attachment(photo_url)
            if attachment is not None:
                attachments = [attachment]
            else:
                logger.warning("Failed to download Zalo image for chat %s", message.get("chat", {}).get("id"))
        except Exception as exc:
            logger.warning("Error downloading Zalo image: %s", exc)

    if not text and not attachments:
        # If the user sent an image but download failed, give a helpful prompt
        # instead of silently ignoring. The agent can then explain the failure.
        if photo_url:
            text = (
                "I received an image but couldn't download it. "
                "Please try sending it again or add a description."
            )
        else:
            # Ignore non-text events unless they carry a downloadable image
            # For unsupported or sticker/voice without text, skip
            if event_name == "message.unsupported.received":
                return
            # Sticker/voice without actionable content -> skip
            return

    # Image-only message (no caption): give the model a prompt so the vision
    # attachment is not orphaned. The pipeline requires non-empty text.
    if not text and attachments:
        text = "Please describe this image."

    from_info = message.get("from") or {}
    chat_info = message.get("chat") or {}
    if not isinstance(from_info, dict) or not isinstance(chat_info, dict):
        return

    user_id = str(from_info.get("id") or "").strip()
    chat_id = str(chat_info.get("id") or "").strip()
    if not user_id or not chat_id:
        return

    is_private = _is_private_chat(chat_info)

    async def reply(outbound: OutboundMessage) -> Any:
        # Zalo Bot API does NOT support editing messages.
        # API reference at https://bot.zapps.me/docs/ only exposes:
        #   getMe, getUpdates, setWebhook, testWebhook, deleteWebhook,
        #   getWebhookInfo, sendMessage, sendPhoto, sendSticker, sendVoice,
        #   sendChatAction — no editMessage / updateMessage.
        # The generic channel streaming flow in
        # agent/modules/channels/agent_bridge.py normally sends a transient
        # "Processing..." message and then edits it via update_target. For
        # Telegram/Discord that edit is supported, but for Zalo it would
        # create an orphan "Processing..." followed by a second message.
        # So we suppress ALL "Processing..." status messages entirely and
        # optionally show a typing indicator via sendChatAction instead.
        # Final responses (non-Processing) are sent as a fresh message.
        # Use exact status pattern from agent_bridge.format_status_text to avoid
        # suppressing legitimate final answers that happen to start with
        # "Processing...". Status is always exactly "Processing..." or
        # "Processing...\n- ..." (tool list).
        is_status = outbound.text == "Processing..." or outbound.text.startswith(
            "Processing...\n-"
        )
        if is_status:
            try:
                await send_zalo_chat_action(chat_id, "typing")
            except Exception:
                logger.debug("Failed to send Zalo typing indicator.", exc_info=True)
            return outbound.update_target
        sent = await send_zalo_message(chat_id, outbound.text, mode=outbound.mode)
        return sent[0] if sent else outbound.update_target

    inbound = InboundMessage(
        platform=get_zalo_adapter().name,
        user_id=user_id,
        channel_id=chat_id,
        text=text,
        is_private=is_private,
        raw=payload,
        reply=reply,
        attachments=attachments,
    )
    await process_inbound_message(inbound, adapter=get_zalo_adapter())


__all__ = ["ZaloChannelAdapter", "get_zalo_adapter", "handle_zalo_update"]
