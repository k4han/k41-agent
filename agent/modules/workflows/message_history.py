from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage


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


def _human_text_from_part(part: Any) -> tuple[str, bool]:
    if isinstance(part, str):
        return part, True

    if isinstance(part, dict):
        part_type = str(part.get("type", "") or "").strip().lower()
        if part_type != "text":
            return "", False

        text_value = part.get("text")
        if isinstance(text_value, str):
            return text_value, True

        return "", False

    text_attr = getattr(part, "text", None)
    if isinstance(text_attr, str):
        return text_attr, True

    return "", False


def _human_text_from_content_list(content: list[Any]) -> tuple[str, bool]:
    if not content:
        return "", False

    text_parts: list[str] = []
    for part in content:
        text, handled = _human_text_from_part(part)
        if not handled:
            return "", False
        text_parts.append(text)

    return "\n\n".join(text_parts).strip(), True


def _tool_metadata_value(part: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = part.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _tool_omitted_content_text(part: dict[str, Any]) -> str:
    part_type = str(part.get("type") or "content").strip().lower()
    metadata: list[str] = []

    mime_type = _tool_metadata_value(part, "mime_type", "mimeType")
    if mime_type:
        metadata.append(f"mime_type={mime_type}")

    if _tool_metadata_value(part, "base64", "data"):
        metadata.append("source=base64")

    url = _tool_metadata_value(part, "url")
    if url:
        if url.startswith("data:"):
            metadata.append("source=data_url")
        else:
            metadata.append(f"url={url}")

    file_id = _tool_metadata_value(part, "file_id", "id")
    if file_id:
        metadata.append(f"id={file_id}")

    suffix = f": {', '.join(metadata)}" if metadata else ""
    return f"[{part_type} content omitted{suffix}]"


def _tool_text_from_part(part: Any) -> str:
    text, handled = _human_text_from_part(part)
    if handled:
        return text

    if isinstance(part, dict):
        content_value = part.get("content")
        if isinstance(content_value, str):
            return content_value
        if isinstance(content_value, list):
            return _tool_text_from_content_list(content_value)
        return _tool_omitted_content_text(part)

    return str(part)


def _tool_text_from_content_list(content: list[Any]) -> str:
    text_parts = [_tool_text_from_part(part) for part in content]
    text_content = "\n\n".join(part for part in text_parts if part).strip()
    return text_content or "[tool returned non-text content]"


def normalize_messages_for_chat_model(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Normalize persisted message history before sending it to chat providers."""
    normalized_messages: list[BaseMessage] = []

    for message in messages:
        content = getattr(message, "content", None)
        if isinstance(message, HumanMessage) and isinstance(content, list):
            text_content, handled = _human_text_from_content_list(content)
            if handled:
                normalized_messages.append(
                    message.model_copy(update={"content": text_content})
                )
                continue

        if isinstance(message, ToolMessage) and isinstance(content, list):
            normalized_messages.append(
                message.model_copy(
                    update={"content": _tool_text_from_content_list(content)}
                )
            )
            continue

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
