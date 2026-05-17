from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage


_SKIPPED_ASSISTANT_BLOCK_TYPES = {
    "reasoning_content",
    "thinking",
    "tool_use",
}


def _assistant_text_from_part(part: Any) -> tuple[str, bool]:
    if isinstance(part, str):
        return part, True

    if isinstance(part, dict):
        part_type = str(part.get("type", "") or "").strip().lower()
        if part_type in _SKIPPED_ASSISTANT_BLOCK_TYPES:
            return "", True

        text_value = part.get("text")
        if isinstance(text_value, str):
            return text_value, True

        content_value = part.get("content")
        if isinstance(content_value, str):
            return content_value, True
        if isinstance(content_value, list):
            return _assistant_text_from_content_list(content_value)

        return "", False

    text_attr = getattr(part, "text", None)
    if isinstance(text_attr, str):
        return text_attr, True

    return "", False


def _assistant_text_from_content_list(content: list[Any]) -> tuple[str, bool]:
    text_parts: list[str] = []
    saw_text_or_skipped_block = False

    for part in content:
        text, handled = _assistant_text_from_part(part)
        if handled:
            saw_text_or_skipped_block = True
            text_parts.append(text)

    return "".join(text_parts), saw_text_or_skipped_block


def normalize_messages_for_chat_model(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Normalize persisted assistant history before sending it to chat providers."""
    normalized_messages: list[BaseMessage] = []

    for message in messages:
        content = getattr(message, "content", None)
        if isinstance(message, AIMessage) and isinstance(content, list):
            text_content, handled = _assistant_text_from_content_list(content)
            if handled:
                normalized_messages.append(
                    message.model_copy(update={"content": text_content})
                )
                continue

        normalized_messages.append(message)

    return normalized_messages


__all__ = ["normalize_messages_for_chat_model"]
