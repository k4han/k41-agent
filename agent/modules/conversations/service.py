from __future__ import annotations

import asyncio
from collections.abc import Sequence
import logging
import re
import uuid
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.modules.agent_runtime import SessionManager
from agent.modules.agents import get_catalog_service
from agent.modules.conversations.repository import (
    ConversationThreadRepository,
    get_conversation_thread_repository,
)
from agent.modules.providers import get_chat_model
from agent.shared.infrastructure.parsing import extract_final_text_content

THREAD_KIND_USER = "user"
THREAD_KIND_SUB_AGENT = "sub_agent"
THREAD_KIND_BACKGROUND = "background"
THREAD_KIND_SCHEDULED = "scheduled"
CONVERSATION_TITLE_AGENT_NAME = "conversation-title"
CONVERSATION_TITLE_TIMEOUT_SECONDS = 8.0
CONVERSATION_TITLE_MAX_CHARS = 80

logger = logging.getLogger(__name__)


def _collapse_whitespace(value: str) -> str:
    return " ".join(str(value or "").split())


def _fallback_conversation_title(first_user_message: str) -> str:
    title = _collapse_whitespace(first_user_message).strip()
    if not title:
        return "New conversation"
    return title[:255]


def _sanitize_generated_title(value: object, fallback: str) -> str:
    text = extract_final_text_content(value)
    for line in text.replace("\r", "\n").split("\n"):
        candidate = _collapse_whitespace(line).strip()
        if not candidate:
            continue
        candidate = re.sub(
            r"^(?:title|conversation title)\s*:\s*",
            "",
            candidate,
            flags=re.I,
        )
        candidate = candidate.strip(" \"'`")
        candidate = candidate.rstrip(" .!?;:")
        if candidate:
            return candidate[:CONVERSATION_TITLE_MAX_CHARS]
    return fallback


def _attachment_title_context(attachments: list[Any] | None) -> str:
    if not attachments:
        return ""

    lines = []
    for index, attachment in enumerate(attachments, start=1):
        data = attachment if isinstance(attachment, dict) else {}
        dump = getattr(attachment, "model_dump", None)
        if callable(dump):
            dumped = dump()
            if isinstance(dumped, dict):
                data = dumped
        name = str(data.get("name") or f"attachment-{index}").strip()
        kind = str(data.get("kind") or "file").strip()
        mime_type = str(data.get("mime_type") or "").strip()
        parts = [kind]
        if mime_type:
            parts.append(mime_type)
        lines.append(f"- {name} ({', '.join(parts)})")
    return "\n".join(lines)


def _build_title_user_prompt(
    *,
    first_user_message: str,
    attachments: list[Any] | None = None,
) -> str:
    prompt = (
        "Create a title for this conversation from the first user message.\n\n"
        f"User message:\n{first_user_message.strip() or '[empty]'}"
    )
    attachment_context = _attachment_title_context(attachments)
    if attachment_context:
        prompt = f"{prompt}\n\nAttachments:\n{attachment_context}"
    return prompt


async def generate_conversation_title(
    *,
    first_user_message: str,
    attachments: list[Any] | None = None,
    timeout_seconds: float = CONVERSATION_TITLE_TIMEOUT_SECONDS,
) -> str:
    fallback = _fallback_conversation_title(first_user_message)

    try:
        catalog = get_catalog_service()
        agent_config = catalog.get_agent(CONVERSATION_TITLE_AGENT_NAME)
        if agent_config is None:
            return fallback

        llm = get_chat_model(
            provider_name=agent_config.provider,
            model=agent_config.model or None,
        )
        messages = [
            SystemMessage(content=agent_config.system_prompt),
            HumanMessage(
                content=_build_title_user_prompt(
                    first_user_message=first_user_message,
                    attachments=attachments,
                )
            ),
        ]
        response = await asyncio.wait_for(
            asyncio.to_thread(llm.invoke, messages),
            timeout=timeout_seconds,
        )
        return _sanitize_generated_title(
            getattr(response, "content", response),
            fallback,
        )
    except Exception as exc:
        logger.debug("Failed to generate conversation title: %s", exc)
        return fallback


def create_thread_id(
    *,
    platform: str,
    user_id: str,
    channel_id: str | None = None,
) -> str:
    platform_value = getattr(platform, "value", platform)
    resolved_channel_id = channel_id or uuid.uuid4().hex[:12]
    return SessionManager.make_thread_id(
        str(platform_value),
        user_id,
        resolved_channel_id,
    )


def infer_thread_kind(thread_id: str) -> str:
    if ":sub:" in thread_id:
        return THREAD_KIND_SUB_AGENT
    if thread_id.startswith("task_"):
        return THREAD_KIND_BACKGROUND
    if thread_id.startswith("bg_"):
        return THREAD_KIND_SCHEDULED
    return THREAD_KIND_USER


def parse_thread_metadata(thread_id: str) -> dict[str, str]:
    try:
        platform, user_id, channel_id = SessionManager.parse_thread_id(thread_id)
    except ValueError:
        return {"platform": "unknown", "user_id": thread_id, "channel_id": ""}
    return {"platform": platform, "user_id": user_id, "channel_id": channel_id}


async def upsert_conversation_thread(
    *,
    thread_id: str,
    agent_name: str = "",
    title: str = "",
    kind: str | None = None,
    platform: str | None = None,
    user_id: str | None = None,
    channel_id: str | None = None,
    repository: ConversationThreadRepository | None = None,
) -> dict[str, Any]:
    parsed = parse_thread_metadata(thread_id)
    repo = repository or get_conversation_thread_repository()
    return await repo.upsert(
        thread_id=thread_id,
        platform=platform or parsed["platform"],
        user_id=user_id or parsed["user_id"],
        channel_id=channel_id if channel_id is not None else parsed["channel_id"],
        agent_name=agent_name,
        title=title or thread_id,
        kind=kind or infer_thread_kind(thread_id),
    )


async def list_conversation_threads(
    *,
    limit: int | None = None,
    offset: int = 0,
    kind: str | None = THREAD_KIND_USER,
    kinds: Sequence[str] | None = None,
    repository: ConversationThreadRepository | None = None,
) -> list[dict[str, Any]]:
    repo = repository or get_conversation_thread_repository()
    return await repo.list(limit=limit, offset=offset, kind=kind, kinds=kinds)


async def count_conversation_threads(
    *,
    kind: str | None = THREAD_KIND_USER,
    kinds: Sequence[str] | None = None,
    repository: ConversationThreadRepository | None = None,
) -> int:
    repo = repository or get_conversation_thread_repository()
    return await repo.count(kind=kind, kinds=kinds)


async def get_conversation_thread(
    thread_id: str,
    *,
    repository: ConversationThreadRepository | None = None,
) -> dict[str, Any] | None:
    repo = repository or get_conversation_thread_repository()
    return await repo.get(thread_id)


async def rename_conversation_thread(
    thread_id: str,
    title: str,
    *,
    repository: ConversationThreadRepository | None = None,
) -> dict[str, Any]:
    repo = repository or get_conversation_thread_repository()
    renamed = await repo.update_title(thread_id, title)
    if renamed is not None:
        return renamed

    return await upsert_conversation_thread(
        thread_id=thread_id,
        title=title,
        repository=repo,
    )


async def update_conversation_thread_title_if_current(
    *,
    thread_id: str,
    title: str,
    current_titles: Sequence[str],
    repository: ConversationThreadRepository | None = None,
) -> dict[str, Any] | None:
    repo = repository or get_conversation_thread_repository()
    return await repo.update_title_if_current(
        thread_id=thread_id,
        title=title,
        current_titles=current_titles,
    )


async def mark_conversation_thread_deleted(
    thread_id: str,
    *,
    repository: ConversationThreadRepository | None = None,
) -> bool:
    repo = repository or get_conversation_thread_repository()
    return await repo.mark_deleted(thread_id)


__all__ = [
    "CONVERSATION_TITLE_AGENT_NAME",
    "CONVERSATION_TITLE_MAX_CHARS",
    "CONVERSATION_TITLE_TIMEOUT_SECONDS",
    "THREAD_KIND_BACKGROUND",
    "THREAD_KIND_SCHEDULED",
    "THREAD_KIND_SUB_AGENT",
    "THREAD_KIND_USER",
    "count_conversation_threads",
    "create_thread_id",
    "generate_conversation_title",
    "get_conversation_thread",
    "infer_thread_kind",
    "list_conversation_threads",
    "mark_conversation_thread_deleted",
    "parse_thread_metadata",
    "rename_conversation_thread",
    "update_conversation_thread_title_if_current",
    "upsert_conversation_thread",
]
