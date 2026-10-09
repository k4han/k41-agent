import asyncio
import base64
import binascii
from contextlib import contextmanager
import logging
from typing import Any, AsyncGenerator, Iterator
from uuid import uuid4

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.types import Command
from agent.shared.infrastructure.parsing import (
    extract_final_text_content,
    extract_tool_display_content,
    extract_thinking_content,
)
from agent.shared.infrastructure.thinking_parser import (
    ContentWithThinking,
    ThinkingTagStreamParser,
    extract_thinking_from_text,
    parse_content_for_thinking,
    strip_thinking_tags,
)

from agent.modules.agent_runtime.active_sessions import (
    ActiveSession,
    SESSION_STEP_RESPONDING,
    SESSION_STEP_THINKING,
    get_active_session_registry,
    current_session_id_var,
    current_thread_id_var,
)
from agent.shared.thread_ids import SessionManager, resolve_thread_id
from agent.modules.workflows import (
    get_workflow_graph,
    make_run_config,
    make_run_context,
)
from agent.modules.usage import attach_usage_context, build_usage_context
from agent.modules.usage import load_usage_context
from agent.modules.tools import (
    ASK_USER_INTERRUPT_TYPE,
    ASK_USER_TOOL_NAME,
    PLAN_MODE_TOOL_NAME,
    PLAN_REVIEW_INTERRUPT_TYPE,
    AskUserAnswerResumePayload,
    HumanResumePayload,
    PlanModeResumePayload,
)
from agent.modules.workspaces import WorkspaceRef

logger = logging.getLogger(__name__)

MAX_CHAT_ATTACHMENTS = 10
MAX_FILE_ATTACHMENT_BYTES = 30 * 1024 * 1024
MAX_TEXT_ATTACHMENT_BYTES = 30 * 1024 * 1024
MAX_IMAGE_ATTACHMENT_BYTES = 30 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 60 * 1024 * 1024
DEFAULT_ATTACHMENT_PROMPT = "Please review the attached file(s)."


def build_run_params(
    *,
    platform: str,
    user_id: str,
    user_input: str,
    thread_id: str | None = None,
    workflow: str | None = None,
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    working_dir: str | None = None,
    context_compact_threshold: int | None = None,
    channel_context_trim_threshold: int | None = None,
    channel_id: str = "",
    agent_name: str = "default",
    provider: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    allowed_skill_names: list[str] | None = None,
    attachments: list[Any] | None = None,
    resume: bool = False,
    resume_payload: dict[str, Any] | None = None,
    checkpoint_id: str | None = None,
) -> dict[str, Any]:
    """Build run parameters for agent execution.

    All config is loaded from agent_name, with optional overrides.

    Args:
        platform: Platform identifier (telegram, discord, api, etc.)
        user_id: User identifier
        user_input: User message
        thread_id: Existing session thread ID to resume
        workflow: Override agent's graph_type if needed
        workspace: Workspace reference for tools
        context_compact_threshold: Override agent's context_compact_threshold if needed
        channel_context_trim_threshold: Optional channel conversation token budget
        channel_id: Channel identifier (for multi-channel platforms)
        agent_name: Agent to use (loads config from catalog)
        provider: Override agent card provider for this run if needed
        model: Override agent card model for this run if needed
        attachments: Optional files attached to the user message
        resume: Request to resume execution from the last checkpoint
    """
    if resume_payload is not None:
        resume = True
    platform_name = str(getattr(platform, "value", platform) or "")
    if channel_context_trim_threshold is None and platform_name in {"telegram", "discord", "zalo"}:
        from agent.shared.config import get_config_service
        channel_context_trim_threshold = get_config_service().get_int(
            f"channels.{platform_name}.context_trim_threshold", 50_000,
        )
    params: dict[str, Any] = {
        "user_input": user_input,
        "thread_id": resolve_thread_id(thread_id) if thread_id else SessionManager.make_thread_id(platform, user_id, channel_id),
        "agent_name": agent_name,
        "workflow": workflow,
        "workspace": workspace if workspace is not None else working_dir,
        "context_compact_threshold": context_compact_threshold,
        "channel_context_trim_threshold": channel_context_trim_threshold,
        "provider": provider,
        "model": model,
        "allowed_skill_names": allowed_skill_names,
        "resume": resume,
        "usage_context": {
            "platform": str(getattr(platform, "value", platform) or ""),
            "user_id": str(user_id or ""),
            "channel_id": str(channel_id or ""),
        },
    }
    if reasoning_effort is not None:
        params["reasoning_effort"] = reasoning_effort
    if checkpoint_id:
        params["checkpoint_id"] = checkpoint_id
    if resume_payload is not None:
        params["resume_payload"] = dict(resume_payload)
    normalized_attachments = _normalize_chat_attachments(attachments)
    if normalized_attachments:
        params["attachments"] = normalized_attachments
    return params


def _model_dump(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        result = dump()
        if isinstance(result, dict):
            return result
    return {}


def _strip_data_url_base64(value: str) -> str:
    if value.startswith("data:") and "," in value:
        return value.split(",", 1)[1]
    return value


def _clean_base64(value: str) -> str:
    return "".join(_strip_data_url_base64(value).split())


def _base64_size(value: str) -> int:
    clean_value = _clean_base64(value)
    if not clean_value:
        return 0
    padding = len(clean_value) - len(clean_value.rstrip("="))
    return max(0, (len(clean_value) * 3 // 4) - padding)


def _decode_image_base64(value: str, *, name: str) -> tuple[str, int]:
    clean_value = _clean_base64(value)
    if not clean_value:
        raise ValueError(f"Image attachment '{name}' is missing data.")

    estimated_size = _base64_size(clean_value)
    if estimated_size > MAX_IMAGE_ATTACHMENT_BYTES:
        raise ValueError(f"Image attachment '{name}' is too large.")

    try:
        decoded = base64.b64decode(clean_value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Image attachment '{name}' has invalid data.") from exc
    size = len(decoded)
    if size > MAX_IMAGE_ATTACHMENT_BYTES:
        raise ValueError(f"Image attachment '{name}' is too large.")
    return clean_value, size


def _normalize_chat_attachments(attachments: list[Any] | None) -> list[dict[str, Any]]:
    if not attachments:
        return []
    if len(attachments) > MAX_CHAT_ATTACHMENTS:
        raise ValueError(f"At most {MAX_CHAT_ATTACHMENTS} files can be attached.")

    total_size = 0
    normalized: list[dict[str, Any]] = []
    for index, attachment in enumerate(attachments, start=1):
        data = _model_dump(attachment)
        kind = str(data.get("kind") or "").strip().lower()
        if kind not in {"text", "image", "file"}:
            raise ValueError("Only text, image, and file attachments are supported.")

        name = str(data.get("name") or "").strip() or f"attachment-{index}"
        mime_type = str(data.get("mime_type") or "").strip()

        if kind == "text":
            content = data.get("content")
            if not isinstance(content, str):
                raise ValueError(f"Text attachment '{name}' is missing content.")
            size = len(content.encode("utf-8"))
            if size > MAX_TEXT_ATTACHMENT_BYTES:
                raise ValueError(f"Text attachment '{name}' is too large.")
            normalized.append(
                {
                    "name": name,
                    "mime_type": mime_type or "text/plain",
                    "size": size,
                    "kind": "text",
                    "content": content,
                }
            )
        elif kind == "file":
            base64_value = data.get("base64")
            content = data.get("content")
            if base64_value and isinstance(base64_value, str) and base64_value.strip():
                try:
                    raw_bytes = base64.b64decode(base64_value)
                except Exception as exc:
                    raise ValueError(f"File attachment '{name}' has invalid base64 data.") from exc
                size = len(raw_bytes)
            elif content and isinstance(content, str):
                raw_bytes = content.encode("utf-8")
                base64_value = base64.b64encode(raw_bytes).decode("ascii")
                size = len(raw_bytes)
            else:
                raise ValueError(f"File attachment '{name}' is missing content or base64 data.")

            if size > MAX_FILE_ATTACHMENT_BYTES:
                raise ValueError(f"File attachment '{name}' is too large.")

            normalized.append(
                {
                    "name": name,
                    "mime_type": mime_type or "application/octet-stream",
                    "size": size,
                    "kind": "file",
                    "base64": base64_value,
                }
            )
        else:
            base64_value = data.get("base64")
            if not isinstance(base64_value, str) or not base64_value.strip():
                raise ValueError(f"Image attachment '{name}' is missing data.")
            base64_value, size = _decode_image_base64(base64_value, name=name)
            normalized.append(
                {
                    "name": name,
                    "mime_type": mime_type or "image/png",
                    "size": size,
                    "kind": "image",
                    "base64": base64_value,
                }
            )

        total_size += size
        if total_size > MAX_TOTAL_ATTACHMENT_BYTES:
            raise ValueError("Attached files exceed the total payload limit.")

    return normalized


def _attachment_metadata(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for attachment in attachments:
        result.append(
            {
                "name": attachment["name"],
                "mime_type": attachment["mime_type"],
                "size": attachment["size"],
                "kind": attachment["kind"],
            }
        )
    return result


def _text_attachment_block(attachment: dict[str, Any]) -> dict[str, str]:
    text = (
        f"Attached text file: {attachment['name']}\n"
        f"MIME type: {attachment['mime_type']}\n"
        f"Size: {attachment['size']} bytes\n\n"
        f"{attachment['content']}"
    )
    return {"type": "text", "text": text}


def _file_attachment_block(attachment: dict[str, Any]) -> dict[str, str]:
    path = f".k41-agent/uploads/{attachment['name']}"
    text = (
        f"Attached file: {attachment['name']}\n"
        f"Workspace path: {path}\n"
        f"MIME type: {attachment['mime_type']}\n"
        f"Size: {attachment['size']} bytes\n\n"
        f"Note: This file is stored in your workspace at '{path}'. You can inspect and process it using bash (e.g. Python scripts with pandas, openpyxl, etc.) or filesystem tools."
    )
    return {"type": "text", "text": text}


def _image_metadata_block(attachment: dict[str, Any]) -> dict[str, str]:
    text = (
        f"Attached image: {attachment['name']}\n"
        f"MIME type: {attachment['mime_type']}\n"
        f"Size: {attachment['size']} bytes"
    )
    return {"type": "text", "text": text}


def _ingest_attachments_to_workspace(
    attachments: list[dict[str, Any]] | None,
    workspace: Any,
) -> None:
    if not attachments or workspace is None:
        return
    try:
        from agent.modules.tools import ingest_attachment_file
        from agent.modules.workspaces import derive_workspace_scope, resolve_workspace_ref

        ref = resolve_workspace_ref(workspace)
        scope = derive_workspace_scope(ref)
        ws_root = ref.locator if ref.backend == "local" else None

        for attachment in attachments:
            name = str(attachment.get("name") or "").strip()
            if not name:
                continue
            content_bytes: bytes | None = None
            if attachment.get("base64"):
                content_bytes = base64.b64decode(attachment["base64"])
            elif attachment.get("content"):
                content_bytes = str(attachment["content"]).encode("utf-8")
            if content_bytes is not None:
                ingest_attachment_file(
                    filename=name,
                    content_bytes=content_bytes,
                    workspace_scope=scope,
                    workspace_root=ws_root,
                )
    except Exception as exc:
        logger.debug("Failed to ingest attachments into workspace: %s", exc)


async def _ingest_attachments_to_sandbox(
    attachments: list[Any] | None,
    workspace: Any,
    *,
    thread_id: str | None = None,
) -> None:
    """Persist attachments into a sandbox's ``.k41-agent`` mount.

    For ``modal``/``daytona`` backends the physical workspace lives inside the
    remote sandbox and cannot be populated by the synchronous host-only
    :func:`_ingest_attachments_to_workspace`. This helper ensures the remote
    ``.k41-agent`` directory exists and copies each attachment file into
    ``.k41-agent/uploads`` via the workspace backend.
    """
    if not attachments or workspace is None:
        return
    try:
        from agent.modules.workspaces import resolve_workspace_ref

        ref = resolve_workspace_ref(workspace)
        if ref.backend not in {"daytona", "modal"}:
            return
        from agent.modules.tools import (
            ensure_sandbox_workspace_storage,
            ingest_attachment_file_to_sandbox,
        )

        normalized = _normalize_chat_attachments(attachments)
        if not normalized:
            return
        await ensure_sandbox_workspace_storage(ref, thread_id=thread_id)
        for attachment in normalized:
            name = str(attachment.get("name") or "").strip()
            if not name:
                continue
            content_bytes: bytes | None = None
            if attachment.get("base64"):
                try:
                    content_bytes = base64.b64decode(attachment["base64"])
                except Exception:
                    continue
            elif attachment.get("content"):
                content_bytes = str(attachment["content"]).encode("utf-8")
            if content_bytes is not None:
                await ingest_attachment_file_to_sandbox(
                    name, content_bytes, ref, thread_id=thread_id
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to ingest sandbox attachments: %s", exc)


def _make_user_message(
    user_input: str,
    attachments: list[Any] | None = None,
    workspace: Any = None,
) -> HumanMessage:
    message_id = f"user-{uuid4()}"
    normalized_attachments = _normalize_chat_attachments(attachments)
    if not normalized_attachments:
        return HumanMessage(content=user_input, id=message_id)

    if workspace is not None:
        _ingest_attachments_to_workspace(normalized_attachments, workspace)

    content_blocks: list[dict[str, str]] = [
        {
            "type": "text",
            "text": user_input.strip() or DEFAULT_ATTACHMENT_PROMPT,
        }
    ]
    for attachment in normalized_attachments:
        if attachment["kind"] == "text":
            content_blocks.append(_text_attachment_block(attachment))
            continue
        if attachment["kind"] == "file":
            content_blocks.append(_file_attachment_block(attachment))
            continue
        content_blocks.append(_image_metadata_block(attachment))
        content_blocks.append(
            {
                "type": "image",
                "base64": attachment["base64"],
                "mime_type": attachment["mime_type"],
            }
        )

    return HumanMessage(
        content=content_blocks,
        id=message_id,
        additional_kwargs={"attachments": _attachment_metadata(normalized_attachments)},
    )


async def clear_agent_session(
    *,
    platform: str,
    user_id: str,
    channel_id: str = "",
) -> None:
    """Clear the session history (checkpoint thread) for a specific user and channel."""
    from agent.modules.tools import close_thread_shell_sessions
    from agent.modules.workspaces import delete_thread_workspace
    from agent.modules.workflows import delete_workflow_thread_tree

    thread_id = SessionManager.make_thread_id(platform, user_id, channel_id)
    close_thread_shell_sessions(thread_id)
    await delete_thread_workspace(thread_id)
    await delete_workflow_thread_tree(thread_id)


def _graph_accepts_context(graph: Any) -> bool:
    context_schema = getattr(graph, "context_schema", Ellipsis)
    if context_schema is Ellipsis:
        return True
    return context_schema is not None


async def _record_conversation_thread(
    *,
    thread_id: str,
    agent_name: str,
    provider: str | None = None,
    model: str | None = None,
    title: str = "",
    attachments: list[Any] | None = None,
    usage_context: dict[str, Any] | None = None,
) -> asyncio.Task[dict[str, Any] | None] | None:
    """Persist thread metadata and schedule title generation.

    Returns the scheduled title-generation task (resolving to the updated
    thread metadata, or ``None``) so stream runners can surface the rename
    in real time while the model is still generating its response.
    """
    try:
        from agent.modules.conversations import (
            THREAD_KIND_USER,
            get_conversation_thread,
            infer_thread_kind,
            schedule_conversation_title_generation,
            upsert_conversation_thread,
        )

        existing = await get_conversation_thread(thread_id)
        kind = str((existing or {}).get("kind") or infer_thread_kind(thread_id))
        resolved_title = title
        should_generate_title = False
        if kind == THREAD_KIND_USER:
            existing_title = str((existing or {}).get("title") or "").strip()
            if existing_title and existing_title != thread_id:
                resolved_title = ""
            else:
                should_generate_title = True

        identity = {
            key: value for key, value in (usage_context or {}).items()
            if key in {"platform", "user_id", "channel_id"}
        }
        await upsert_conversation_thread(
            thread_id=thread_id,
            agent_name=agent_name,
            provider=provider,
            model=model,
            title=resolved_title,
            kind=kind,
            **identity,
        )
        if should_generate_title:
            return schedule_conversation_title_generation(
                thread_id=thread_id,
                title=title,
                attachments=attachments,
            )
    except Exception as exc:
        logger.debug(
            "Failed to record conversation thread '%s': %s",
            thread_id,
            exc,
        )
    return None


def _coerce_stream_event(event: Any) -> tuple[str, Any]:
    if isinstance(event, tuple):
        if len(event) == 2 and isinstance(event[0], str):
            return event[0], event[1]
        if len(event) == 3 and isinstance(event[1], str):
            return event[1], event[2]
    return "values", event


class _StreamingChunkExtractor:
    """Extract (visible, thinking) deltas from a stream of ``AIMessageChunk`` events.

    The extractor carries enough state to handle ``<thinking>...</thinking>``
    tags that span two adjacent chunks. Structured ``type: "thinking"`` parts
    are routed to the thinking bucket directly while inline tags inside string
    content are split by :class:`ThinkingTagStreamParser`. This abstraction
    lets the runner always strip thinking from the visible stream while still
    being able to optionally surface those deltas through a separate event
    when the UI opts in.
    """

    def __init__(self) -> None:
        self._tag_parser = ThinkingTagStreamParser()

    def extract(self, event: Any) -> ContentWithThinking:
        metadata = event[1] if isinstance(event, tuple) and len(event) > 1 else {}
        if isinstance(metadata, dict) and (
            metadata.get("context_compaction") or "context_compaction" in metadata.get("tags", [])
        ):
            return ContentWithThinking(text="", thinking="")
        chunk = event[0] if isinstance(event, tuple) and event else event
        if not isinstance(chunk, AIMessageChunk):
            return ContentWithThinking(text="", thinking="")

        content = getattr(chunk, "content", None)
        parts = (
            [_split_chunk_part(part) for part in content]
            if isinstance(content, list)
            else [_split_chunk_part(content)]
        )

        # Classify per-part first then route visible strings through the
        # stateful tag parser so that ``<thinking>...</thinking>`` can be
        # handled when the open/close tag is split across chunk boundaries.
        visible_strings = "".join(part.text for part in parts if part.text)
        visible_delta = self._tag_parser.feed(visible_strings)
        thinking_delta = "".join(part.thinking for part in parts if part.thinking)
        if thinking_delta:
            return ContentWithThinking(
                text=visible_delta.text,
                thinking=visible_delta.thinking + thinking_delta,
            )
        return visible_delta

    def flush(self) -> ContentWithThinking:
        return self._tag_parser.flush()


def _split_chunk_part(value: Any) -> ContentWithThinking:
    """Classify a single chunk ``content`` part into (visible, thinking)."""
    if value is None:
        return ContentWithThinking(text="", thinking="")
    if isinstance(value, str):
        # The streaming parser handles cross-chunk tag boundaries; for a
        # single self-contained string we classify inline tags here.
        return ContentWithThinking(
            text=strip_thinking_tags(value),
            thinking=extract_thinking_from_text(value),
        )
    if isinstance(value, dict):
        part_type = str(value.get("type", "") or "").strip().lower()
        if part_type in {"thinking", "reasoning", "reasoning_content"}:
            for key in ("thinking", "reasoning", "reasoning_content"):
                nested = value.get(key)
                if isinstance(nested, str) and nested:
                    return ContentWithThinking(text="", thinking=nested)
            nested = value.get("text") or value.get("content")
            return ContentWithThinking(text="", thinking=_coerce_thinking_value(nested))
        text_value = value.get("text")
        if isinstance(text_value, str):
            return ContentWithThinking(
                text=strip_thinking_tags(text_value),
                thinking=extract_thinking_from_text(text_value),
            )
        nested_content = value.get("content")
        if nested_content is not None:
            return parse_content_for_thinking(nested_content)
        return ContentWithThinking(text="", thinking="")
    thinking_attr = (
        getattr(value, "thinking", None)
        or getattr(value, "reasoning", None)
        or getattr(value, "reasoning_content", None)
    )
    if isinstance(thinking_attr, str) and thinking_attr:
        return ContentWithThinking(text="", thinking=thinking_attr)
    text_attr = getattr(value, "text", None)
    if isinstance(text_attr, str):
        return ContentWithThinking(
            text=strip_thinking_tags(text_attr),
            thinking=extract_thinking_from_text(text_attr),
        )
    content_attr = getattr(value, "content", None)
    if content_attr is not None:
        return parse_content_for_thinking(content_attr)
    return ContentWithThinking(text="", thinking="")


def _coerce_thinking_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return parse_content_for_thinking(value).thinking


def _extract_message_chunk_content(event: Any) -> str:
    """Extract visible text from a chunked ``AIMessageChunk`` event.

    Used by call sites that need the legacy single-string representation
    and do not care about thinking deltas. Internally still strips inline
    ``<thinking>...</thinking>`` tags from the resulting string.
    """
    chunk = event[0] if isinstance(event, tuple) and event else event
    if not isinstance(chunk, AIMessageChunk):
        return ""

    content = getattr(chunk, "content", None)
    parts = (
        [_split_chunk_part(part) for part in content]
        if isinstance(content, list)
        else [_split_chunk_part(content)]
    )
    return "".join(part.text for part in parts if part.text)


def _message_id(message: Any) -> str:
    return str(getattr(message, "id", "") or "")


def _configurable(config: dict[str, Any]) -> dict[str, Any]:
    configurable = config.setdefault("configurable", {})
    if not isinstance(configurable, dict):
        configurable = {}
        config["configurable"] = configurable
    return configurable


def _config_checkpoint_id(config: dict[str, Any] | None) -> str:
    if not isinstance(config, dict):
        return ""
    configurable = config.get("configurable", {})
    if not isinstance(configurable, dict):
        return ""
    return str(configurable.get("checkpoint_id", "") or "")


def _config_with_checkpoint(config: dict[str, Any], checkpoint_id: str | None) -> dict[str, Any]:
    if not checkpoint_id:
        return config
    next_config = {**config, "configurable": dict(config.get("configurable", {}))}
    _configurable(next_config)["checkpoint_id"] = checkpoint_id
    _configurable(next_config).setdefault("checkpoint_ns", "")
    return next_config


def _normalize_plan_resume_payload(
    resume_payload: dict[str, Any] | None,
) -> PlanModeResumePayload | None:
    if resume_payload is None:
        return None
    return PlanModeResumePayload.model_validate(resume_payload)


def _normalize_human_resume_payload(
    resume_payload: dict[str, Any] | None,
) -> HumanResumePayload | None:
    if resume_payload is None:
        return None
    action = str(resume_payload.get("action") or "").strip()
    if action == "permission":
        from agent.modules.tools import PermissionResume
        return PermissionResume.model_validate(resume_payload)
    if action == "answer":
        return AskUserAnswerResumePayload.model_validate(resume_payload)
    return _normalize_plan_resume_payload(resume_payload)


def _resolve_agent_name_for_resume(
    catalog: Any,
    agent_name: str,
    resume_payload: HumanResumePayload | None,
    *,
    source_agent_name: str = "",
) -> str:
    if resume_payload is None or resume_payload.action != "approve":
        return agent_name

    target_agent = str(resume_payload.target_agent or "").strip()
    if not target_agent:
        raise ValueError("Target agent is required to approve a plan.")

    target_config = catalog.get_agent(target_agent)
    target_card = catalog.get_agent_card(target_agent)
    if target_config is None or target_card is None:
        raise ValueError(f"Agent '{target_agent}' not found in catalog.")
    if target_card.hidden or not target_card.valid:
        raise ValueError(f"Agent '{target_agent}' cannot be selected for plan approval.")
    source_agent = str(source_agent_name or "").strip()
    if source_agent:
        if target_agent == source_agent:
            raise ValueError("Plan approval target cannot be the planner agent.")
        source_card = catalog.get_agent_card(source_agent)
        if source_card is None:
            raise ValueError(
                f"Agent '{source_agent}' cannot be validated for plan approval."
            )
        allowed_targets = list(getattr(source_card, "plan_approval_targets", []) or [])
        if allowed_targets and target_agent not in allowed_targets:
            raise ValueError(
                f"Agent '{target_agent}' is not allowed as a plan approval target for "
                f"agent '{source_agent}'."
            )
    return target_agent


def _validate_plan_resume_payload(
    resume_payload: PlanModeResumePayload | None,
) -> None:
    if resume_payload is None:
        return
    if resume_payload.action == "approve":
        if not str(resume_payload.target_agent or "").strip():
            raise ValueError("Target agent is required to approve a plan.")
        return
    if not str(resume_payload.feedback or "").strip():
        raise ValueError("Feedback is required to revise a plan.")


def _validate_human_resume_payload(
    resume_payload: HumanResumePayload | None,
) -> None:
    if resume_payload is None:
        return
    if resume_payload.action in {"answer", "permission"}:
        return
    _validate_plan_resume_payload(resume_payload)


async def _update_thread_agent(thread_id: str, agent_name: str) -> None:
    try:
        from agent.modules.conversations import upsert_conversation_thread

        await upsert_conversation_thread(
            thread_id=thread_id,
            agent_name=agent_name,
        )
    except Exception as exc:
        logger.debug(
            "Failed to update conversation thread '%s' agent to '%s': %s",
            thread_id,
            agent_name,
            exc,
        )


async def _get_plan_resume_source_agent_name(
    thread_id: str,
    agent_name: str,
    resume_payload: HumanResumePayload | None,
) -> str:
    if resume_payload is None or resume_payload.action != "approve":
        return ""

    try:
        from agent.modules.conversations import get_conversation_thread

        thread = await get_conversation_thread(thread_id)
    except Exception as exc:
        logger.debug(
            "Failed to load conversation thread '%s' for plan approval validation: %s",
            thread_id,
            exc,
        )
        thread = None

    thread_agent = ""
    if isinstance(thread, dict):
        thread_agent = str(thread.get("agent_name") or "").strip()
    if thread_agent:
        return thread_agent

    target_agent = str(resume_payload.target_agent or "").strip()
    current_agent = str(agent_name or "").strip()
    return current_agent if current_agent and current_agent != target_agent else ""


def _interrupt_value(interrupt_obj: Any) -> Any:
    return getattr(interrupt_obj, "value", None)


def _interrupt_id(interrupt_obj: Any) -> str:
    return str(getattr(interrupt_obj, "id", "") or "")


def _find_tool_call_id(
    messages: list[Any],
    tool_name: str,
    fallback: str = "",
) -> str:
    for message in reversed(messages):
        tool_calls = getattr(message, "tool_calls", None) or []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            if tool_call.get("name") == tool_name:
                return str(tool_call.get("id") or fallback)
    return fallback


def _find_plan_tool_call_id(messages: list[Any], fallback: str = "") -> str:
    return _find_tool_call_id(messages, PLAN_MODE_TOOL_NAME, fallback)


def _plan_review_events_from_value_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    raw_interrupts = event.get("__interrupt__")
    if not raw_interrupts:
        return []
    if not isinstance(raw_interrupts, (list, tuple)):
        raw_interrupts = [raw_interrupts]

    messages = event.get("messages", [])
    messages = messages if isinstance(messages, list) else []
    out: list[dict[str, Any]] = []
    for interrupt_obj in raw_interrupts:
        value = _interrupt_value(interrupt_obj)
        if not isinstance(value, dict):
            continue
        if value.get("type") != PLAN_REVIEW_INTERRUPT_TYPE:
            continue
        tool_call_id = str(value.get("tool_call_id") or "")
        tool_call_id = _find_plan_tool_call_id(messages, tool_call_id)
        out.append(
            {
                "type": PLAN_REVIEW_INTERRUPT_TYPE,
                "tool_call_id": tool_call_id,
                "interrupt_id": _interrupt_id(interrupt_obj),
                "plan": str(value.get("plan") or ""),
            }
        )
    return out


def _user_input_request_events_from_value_event(
    event: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_interrupts = event.get("__interrupt__")
    if not raw_interrupts:
        return []
    if not isinstance(raw_interrupts, (list, tuple)):
        raw_interrupts = [raw_interrupts]

    messages = event.get("messages", [])
    messages = messages if isinstance(messages, list) else []
    out: list[dict[str, Any]] = []
    for interrupt_obj in raw_interrupts:
        value = _interrupt_value(interrupt_obj)
        if not isinstance(value, dict):
            continue
        if value.get("type") == "permission_request":
            from agent.modules.tools import permission_request_event
            out.append(permission_request_event(value, _interrupt_id(interrupt_obj)))
            continue
        if value.get("type") != ASK_USER_INTERRUPT_TYPE:
            continue
        tool_call_id = str(value.get("tool_call_id") or "")
        tool_call_id = _find_tool_call_id(
            messages,
            ASK_USER_TOOL_NAME,
            tool_call_id,
        )
        out.append(
            {
                "type": ASK_USER_INTERRUPT_TYPE,
                "tool_call_id": tool_call_id,
                "interrupt_id": _interrupt_id(interrupt_obj),
                "title": str(value.get("title") or ""),
                "questions": value.get("questions") or [],
                "submit_label": str(value.get("submit_label") or ""),
            }
        )
    return out


def _run_config_from_checkpoint(
    *,
    base_config: dict[str, Any],
    checkpoint_config: dict[str, Any],
) -> dict[str, Any]:
    next_config = {**base_config, "configurable": dict(base_config.get("configurable", {}))}
    checkpoint_configurable = checkpoint_config.get("configurable", {})
    if isinstance(checkpoint_configurable, dict):
        _configurable(next_config).update(checkpoint_configurable)
    return next_config


def _replace_human_message_text(message: HumanMessage, text: str) -> HumanMessage:
    content = message.content
    if isinstance(content, list):
        attachment_parts: list[Any] = []
        for part in content:
            if isinstance(part, dict):
                part_type = str(part.get("type") or "").strip().lower()
                if part_type == "image_url":
                    attachment_parts.append(part)
                    continue
                if part_type == "text":
                    text_value = part.get("text")
                    if isinstance(text_value, str) and text_value.startswith("Attached "):
                        attachment_parts.append(part)
                        continue
                    # Skip original user text blocks; they will be replaced.
                    continue
                attachment_parts.append(part)
            else:
                attachment_parts.append(part)
        next_content: list[Any] = []
        next_content.extend(attachment_parts)
        next_content.append({"type": "text", "text": text})
        return message.model_copy(update={"content": next_content})
    return message.model_copy(update={"content": text})


async def _find_message_source_state(
    graph: Any,
    *,
    config: dict[str, Any],
    source_checkpoint_id: str,
    message_index: int,
):
    async for state in graph.aget_state_history(config):
        if _config_checkpoint_id(getattr(state, "config", None)) != source_checkpoint_id:
            continue
        messages = (getattr(state, "values", {}) or {}).get("messages", [])
        if message_index >= len(messages):
            raise ValueError("Message index is outside the checkpoint state.")
        message = messages[message_index]
        if not isinstance(message, HumanMessage):
            raise ValueError("Only user messages can be edited.")
        parent_config = getattr(state, "parent_config", None)
        if not parent_config:
            raise ValueError("Cannot edit the first checkpoint in a thread.")
        return state, message

    raise ValueError("Source checkpoint was not found.")


@contextmanager
def track_active_session(
    thread_id: str, agent_name: str, *, usage_context: dict[str, Any] | None = None,
) -> Iterator[str]:
    """Track an active agent session for a thread.

    Sessions are reference-counted per thread: nested or overlapping runs on
    the same thread reuse the existing session, and the session is
    unregistered only when the last reference is released. This keeps the
    dashboard running state stable for a run's full lifecycle (including
    background task completion hooks and retries).
    """
    import asyncio
    registry = get_active_session_registry()
    identity = build_usage_context(thread_id, usage_context)
    session = ActiveSession(
        thread_id=thread_id,
        platform=identity.platform,
        user_id=identity.user_id,
        channel_id=identity.channel_id,
        agent_name=agent_name,
    )

    current_task = None
    try:
        current_task = asyncio.current_task()
    except RuntimeError:
        pass

    session_id = registry.acquire(session, task=current_task)
    session_token = current_session_id_var.set(session_id)
    thread_token = current_thread_id_var.set(thread_id)
    try:
        registry.update_step(session_id, SESSION_STEP_THINKING)
        yield session_id
    finally:
        current_thread_id_var.reset(thread_token)
        current_session_id_var.reset(session_token)
        registry.release(session_id)


async def run_agent(
    user_input: str,
    thread_id: str,
    agent_name: str = "default",
    *,
    workflow: str | None = None,
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    working_dir: str | None = None,
    context_compact_threshold: int | None = None,
    channel_context_trim_threshold: int | None = None,
    allowed_tool_names: list[str] | None = None,
    allowed_skill_names: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    attachments: list[Any] | None = None,
    usage_context: dict[str, Any] | None = None,
    resume: bool = False,
    resume_payload: dict[str, Any] | None = None,
    checkpoint_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """Run a workflow graph and stream assistant chunks.

    Loads full config from agent_name, allows selective overrides.

    Args:
        user_input: User message
        thread_id: Session thread ID
        agent_name: Agent to use (loads config from catalog)
        workflow: Override agent's graph_type if needed
        workspace: Workspace reference for tools
        context_compact_threshold: Override agent's context_compact_threshold if needed
        channel_context_trim_threshold: Optional channel conversation token budget
        allowed_tool_names: Override agent's tools if needed
        provider: Override agent card provider for this run if needed
        model: Override agent card model for this run if needed
        attachments: Optional files attached to the user message
    """
    from agent.modules.agents import get_catalog_service

    catalog = get_catalog_service()
    normalized_resume_payload = _normalize_human_resume_payload(resume_payload)
    _validate_human_resume_payload(normalized_resume_payload)
    source_agent_name = await _get_plan_resume_source_agent_name(
        thread_id,
        agent_name,
        normalized_resume_payload,
    )
    agent_name = _resolve_agent_name_for_resume(
        catalog,
        agent_name,
        normalized_resume_payload,
        source_agent_name=source_agent_name,
    )
    if normalized_resume_payload is not None:
        resume = True
        # When resuming an interrupt (plan review / ask_user), ignore any
        # stale checkpoint_id from the client. LangGraph tracks the pending
        # interrupt on the latest checkpoint, so a stale id would resume from
        # the wrong state and appear as "stopped" after approval.
        checkpoint_id = None
    agent_config = catalog.get_agent(agent_name)
    if agent_config is None:
        raise ValueError(f"Agent '{agent_name}' not found in catalog")

    # Resolve: explicit params > agent config
    resolved_workflow = workflow or agent_config.graph_type
    resolved_tools = allowed_tool_names if allowed_tool_names is not None else agent_config.tools

    thread_id = resolve_thread_id(thread_id)
    approval_supported = str((usage_context or {}).get("platform", "")) == "api"
    usage_context = (await load_usage_context(thread_id, usage_context)).to_dict()
    graph = get_workflow_graph(resolved_workflow)
    config = attach_usage_context(
        make_run_config(thread_id=thread_id),
        build_usage_context(thread_id, usage_context),
    )
    config = _config_with_checkpoint(config, checkpoint_id)
    config["configurable"]["approval_supported"] = approval_supported or usage_context["platform"] == "api" or bool(config["configurable"].get("approval_supported"))

    context = make_run_context(
        workspace=workspace,
        working_dir=working_dir,
        context_compact_threshold=context_compact_threshold,
        channel_context_trim_threshold=channel_context_trim_threshold,
        agent_name=agent_name,
        allowed_tool_names=resolved_tools or None,
        allowed_skill_names=allowed_skill_names,
        provider=provider,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    if normalized_resume_payload is not None and normalized_resume_payload.action == "approve":
        await _update_thread_agent(thread_id, agent_name)
    elif not resume and not checkpoint_id:
        await _record_conversation_thread(
            thread_id=thread_id,
            agent_name=agent_name,
            provider=provider,
            model=model,
            title=user_input,
            attachments=attachments,
            usage_context=usage_context,
        )

    stream_kwargs: dict[str, Any] = {
        "config": config,
        "stream_mode": "values",
    }
    if _graph_accepts_context(graph):
        stream_kwargs["context"] = context

    registry = get_active_session_registry()
    # Ensure sandbox ``.k41-agent`` is populated before the graph sees the message.
    if not resume and normalized_resume_payload is None and attachments and workspace is not None:
        await _ingest_attachments_to_sandbox(attachments, workspace, thread_id=thread_id)
    if normalized_resume_payload is not None:
        input_data = Command(resume=normalized_resume_payload.model_dump(exclude_none=True))
    elif resume:
        input_data = None
    else:
        input_data = {"messages": [_make_user_message(user_input, attachments, workspace=workspace)]}

    with track_active_session(thread_id, agent_name, usage_context=usage_context) as session_id:
        async for event in graph.astream(
            input_data,
            **stream_kwargs,
        ):
            messages = event.get("messages", [])
            if messages:
                last = messages[-1]
                if isinstance(last, AIMessage) and not last.additional_kwargs.get("is_compact_summary"):
                    content = extract_final_text_content(getattr(last, "content", None))
                    if content:
                        registry.update_step(session_id, SESSION_STEP_RESPONDING)
                        yield content


async def run_agent_stream(
    user_input: str,
    thread_id: str,
    agent_name: str = "default",
    *,
    workflow: str | None = None,
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    working_dir: str | None = None,
    context_compact_threshold: int | None = None,
    channel_context_trim_threshold: int | None = None,
    allowed_tool_names: list[str] | None = None,
    allowed_skill_names: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    attachments: list[Any] | None = None,
    usage_context: dict[str, Any] | None = None,
    resume: bool = False,
    resume_payload: dict[str, Any] | None = None,
    checkpoint_id: str | None = None,
    emit_thinking: bool = False,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run a workflow graph and stream UI events (tool calls and text chunks).

    Loads full config from agent_name, allows selective overrides.

    Args:
        user_input: User message
        thread_id: Session thread ID
        agent_name: Agent to use (loads config from catalog)
        workflow: Override agent's graph_type if needed
        workspace: Workspace reference for tools
        context_compact_threshold: Override agent's context_compact_threshold if needed
        channel_context_trim_threshold: Optional channel conversation token budget
        allowed_tool_names: Override agent's tools if needed
        provider: Override agent card provider for this run if needed
        model: Override agent card model for this run if needed
        attachments: Optional files attached to the user message
        resume: Request to resume execution from the last checkpoint
        emit_thinking: When ``True``, also yield ``{"type": "thinking", ...}``
            events for reasoning deltas so the UI can render them in real
            time. ``thinking`` is always stripped from the visible text
            events regardless of this flag, so the default ``False`` simply
            suppresses the extra events.
    """
    from agent.modules.agents import get_catalog_service

    catalog = get_catalog_service()
    normalized_resume_payload = _normalize_human_resume_payload(resume_payload)
    _validate_human_resume_payload(normalized_resume_payload)
    source_agent_name = await _get_plan_resume_source_agent_name(
        thread_id,
        agent_name,
        normalized_resume_payload,
    )
    agent_name = _resolve_agent_name_for_resume(
        catalog,
        agent_name,
        normalized_resume_payload,
        source_agent_name=source_agent_name,
    )
    if normalized_resume_payload is not None:
        resume = True
        # When resuming an interrupt (plan review / ask_user), ignore any
        # stale checkpoint_id from the client. LangGraph tracks the pending
        # interrupt on the latest checkpoint, so a stale id would resume from
        # the wrong state and appear as "stopped" after approval.
        checkpoint_id = None
    agent_config = catalog.get_agent(agent_name)
    if agent_config is None:
        raise ValueError(f"Agent '{agent_name}' not found in catalog")

    # Resolve: explicit params > agent config
    resolved_workflow = workflow or agent_config.graph_type
    resolved_tools = allowed_tool_names if allowed_tool_names is not None else agent_config.tools

    thread_id = resolve_thread_id(thread_id)
    approval_supported = str((usage_context or {}).get("platform", "")) == "api"
    usage_context = (await load_usage_context(thread_id, usage_context)).to_dict()
    graph = get_workflow_graph(resolved_workflow)
    config = attach_usage_context(
        make_run_config(thread_id=thread_id),
        build_usage_context(thread_id, usage_context),
    )
    config = _config_with_checkpoint(config, checkpoint_id)
    config["configurable"]["approval_supported"] = approval_supported or usage_context["platform"] == "api" or bool(config["configurable"].get("approval_supported"))

    context = make_run_context(
        workspace=workspace,
        working_dir=working_dir,
        context_compact_threshold=context_compact_threshold,
        channel_context_trim_threshold=channel_context_trim_threshold,
        agent_name=agent_name,
        allowed_tool_names=resolved_tools or None,
        allowed_skill_names=allowed_skill_names,
        provider=provider,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    conversation_title_task: asyncio.Task[dict[str, Any] | None] | None = None
    if normalized_resume_payload is not None and normalized_resume_payload.action == "approve":
        await _update_thread_agent(thread_id, agent_name)
    elif not resume and not checkpoint_id:
        conversation_title_task = await _record_conversation_thread(
            thread_id=thread_id,
            agent_name=agent_name,
            provider=provider,
            model=model,
            title=user_input,
            attachments=attachments,
            usage_context=usage_context,
        )

    seen_ids: set[str] = set()
    if resume:
        try:
            state = await graph.aget_state(config)
            if state and state.values and "messages" in state.values:
                for msg in state.values["messages"]:
                    msg_id = _message_id(msg)
                    if msg_id:
                        seen_ids.add(msg_id)
        except Exception as exc:
            logger.warning("Failed to fetch historical messages for resume: %s", exc)

    stream_kwargs: dict[str, Any] = {
        "config": config,
        "stream_mode": ["messages", "values", "custom"],
    }
    if _graph_accepts_context(graph):
        stream_kwargs["context"] = context

    registry = get_active_session_registry()
    if not resume and normalized_resume_payload is None and attachments and workspace is not None:
        await _ingest_attachments_to_sandbox(attachments, workspace, thread_id=thread_id)
    if resume:
        input_data = None
        user_message_id = None
        current_user_seen = True
        if normalized_resume_payload is not None:
            input_data = Command(
                resume=normalized_resume_payload.model_dump(exclude_none=True)
            )
    else:
        user_message = _make_user_message(user_input, attachments, workspace=workspace)
        input_data = {"messages": [user_message]}
        user_message_id = _message_id(user_message)
        current_user_seen = False

    title_event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def _publish_generated_title() -> None:
        if conversation_title_task is None:
            return
        try:
            updated_metadata = await conversation_title_task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug(
                "Conversation title generation failed for '%s': %s",
                thread_id,
                exc,
            )
            return
        generated_title = str((updated_metadata or {}).get("title") or "").strip()
        if generated_title:
            title_event_queue.put_nowait(
                {
                    "type": "thread_title",
                    "thread_id": thread_id,
                    "title": generated_title,
                }
            )

    title_watcher = asyncio.create_task(_publish_generated_title())
    try:
        with track_active_session(thread_id, agent_name, usage_context=usage_context) as session_id:
            chunk_extractor = _StreamingChunkExtractor()
            async for event in graph.astream(
                input_data,
                **stream_kwargs,
            ):
                stream_mode, event_data = _coerce_stream_event(event)
                while not title_event_queue.empty():
                    yield title_event_queue.get_nowait()
                if stream_mode == "custom":
                    if isinstance(event_data, dict) and event_data.get("type") == "context_usage":
                        event_thread_id = str(event_data.get("thread_id") or "")
                        if not event_thread_id or event_thread_id == thread_id:
                            yield event_data
                        else:
                            logger.debug(
                                "Ignoring context_usage for sub-thread '%s' (main '%s')",
                                event_thread_id,
                                thread_id,
                            )
                    else:
                        logger.debug("Ignoring unsupported custom stream event: %r", event_data)
                    continue
                if stream_mode == "messages":
                    delta = chunk_extractor.extract(event_data)
                    if delta.text:
                        registry.update_step(session_id, SESSION_STEP_RESPONDING)
                        yield {
                            "type": "message",
                            "content": delta.text,
                        }
                    if emit_thinking and delta.thinking:
                        yield {
                            "type": "thinking",
                            "content": delta.thinking,
                        }
                    continue

                if stream_mode != "values":
                    continue

                event = event_data
                for plan_review_event in _plan_review_events_from_value_event(event):
                    yield plan_review_event
                for user_input_event in _user_input_request_events_from_value_event(event):
                    yield user_input_event

                messages = event.get("messages", [])
                if not messages:
                    continue

                if user_message_id:
                    current_user_index = next(
                        (
                            index
                            for index, message in enumerate(messages)
                            if _message_id(message) == user_message_id
                        ),
                        None,
                    )
                    if current_user_index is not None:
                        current_user_seen = True
                        for message in messages[: current_user_index + 1]:
                            message_id = _message_id(message)
                            if message_id:
                                seen_ids.add(message_id)
                        messages = messages[current_user_index + 1 :]
                    elif not current_user_seen and len(messages) > 1:
                        for message in messages:
                            message_id = _message_id(message)
                            if message_id:
                                seen_ids.add(message_id)
                        continue

                for message in messages:
                    message_id = _message_id(message)
                    if message_id:
                        if message_id in seen_ids:
                            continue
                        seen_ids.add(message_id)

                    if getattr(message, "additional_kwargs", {}).get("is_compact_summary"):
                        continue
                    if isinstance(message, AIMessage):
                        tool_calls = getattr(message, "tool_calls", None)
                        content = extract_final_text_content(getattr(message, "content", None))
                        thinking = (
                            extract_thinking_content(getattr(message, "content", None))
                            if emit_thinking
                            else ""
                        )
                        if content:
                            registry.update_step(session_id, SESSION_STEP_RESPONDING)
                            yield {
                                "type": "final",
                                "content": content,
                            }
                        if thinking:
                            yield {
                                "type": "thinking",
                                "content": thinking,
                            }
                        if tool_calls:
                            for tc in tool_calls:
                                tool_name = tc.get("name") or "unknown"
                                if tool_name in {PLAN_MODE_TOOL_NAME, ASK_USER_TOOL_NAME}:
                                    continue
                                registry.add_tool_call(session_id, tool_name)
                                yield {
                                    "type": "tool_call",
                                    "id": tc.get("id"),
                                    "name": tool_name,
                                    "args": tc.get("args"),
                                }
                    elif isinstance(message, ToolMessage):
                        yield {
                            "type": "tool_result",
                            "tool_call_id": getattr(message, "tool_call_id", None),
                            "name": getattr(message, "name", None),
                            "content": extract_tool_display_content(message),
                        }

            # The graph finished but the generated title may still be in
            # flight; give it a bounded window so a late rename still
            # reaches the client before the stream closes.
            if not title_watcher.done():
                try:
                    from agent.modules.conversations import (
                        CONVERSATION_TITLE_TIMEOUT_SECONDS,
                    )

                    await asyncio.wait_for(
                        asyncio.shield(title_watcher),
                        timeout=CONVERSATION_TITLE_TIMEOUT_SECONDS + 2.0,
                    )
                except TimeoutError:
                    pass
            while not title_event_queue.empty():
                yield title_event_queue.get_nowait()
    finally:
        if not title_watcher.done():
            title_watcher.cancel()


async def run_agent_edit_stream(
    user_input: str,
    thread_id: str,
    agent_name: str = "default",
    *,
    message_index: int,
    source_checkpoint_id: str,
    workflow: str | None = None,
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    working_dir: str | None = None,
    context_compact_threshold: int | None = None,
    channel_context_trim_threshold: int | None = None,
    allowed_tool_names: list[str] | None = None,
    allowed_skill_names: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    usage_context: dict[str, Any] | None = None,
    resume: bool = False,
    emit_thinking: bool = False,
) -> AsyncGenerator[dict[str, Any], None]:
    """Fork a thread from the checkpoint before a user message and stream the result."""
    from agent.modules.agents import get_catalog_service

    edited_text = user_input.strip()
    if not edited_text:
        raise ValueError("Edited message cannot be empty.")

    catalog = get_catalog_service()
    agent_config = catalog.get_agent(agent_name)
    if agent_config is None:
        raise ValueError(f"Agent '{agent_name}' not found in catalog")

    resolved_workflow = workflow or agent_config.graph_type
    resolved_tools = allowed_tool_names if allowed_tool_names is not None else agent_config.tools

    thread_id = resolve_thread_id(thread_id)
    approval_supported = str((usage_context or {}).get("platform", "")) == "api"
    usage_context = (await load_usage_context(thread_id, usage_context)).to_dict()
    graph = get_workflow_graph(resolved_workflow)
    base_config = attach_usage_context(
        make_run_config(thread_id=thread_id),
        build_usage_context(thread_id, usage_context),
    )

    base_config["configurable"]["approval_supported"] = approval_supported or usage_context["platform"] == "api" or bool(base_config["configurable"].get("approval_supported"))

    source_state, original_message = await _find_message_source_state(
        graph,
        config=base_config,
        source_checkpoint_id=source_checkpoint_id,
        message_index=message_index,
    )
    edited_message = _replace_human_message_text(original_message, edited_text)
    config = _run_config_from_checkpoint(
        base_config=base_config,
        checkpoint_config=source_state.parent_config,
    )

    context = make_run_context(
        workspace=workspace,
        working_dir=working_dir,
        context_compact_threshold=context_compact_threshold,
        channel_context_trim_threshold=channel_context_trim_threshold,
        agent_name=agent_name,
        allowed_tool_names=resolved_tools or None,
        allowed_skill_names=allowed_skill_names,
        provider=provider,
        model=model,
        reasoning_effort=reasoning_effort,
    )

    seen_ids: set[str] = set()
    parent_values = getattr(source_state, "parent_config", None)
    if parent_values:
        try:
            parent_state = await graph.aget_state(config)
            if parent_state and parent_state.values and "messages" in parent_state.values:
                for msg in parent_state.values["messages"]:
                    msg_id = _message_id(msg)
                    if msg_id:
                        seen_ids.add(msg_id)
        except Exception as exc:
            logger.warning("Failed to fetch parent messages for edit: %s", exc)

    stream_kwargs: dict[str, Any] = {
        "config": config,
        "stream_mode": ["messages", "values", "custom"],
    }
    if _graph_accepts_context(graph):
        stream_kwargs["context"] = context

    registry = get_active_session_registry()
    user_message_id = _message_id(edited_message)
    current_user_seen = False

    with track_active_session(thread_id, agent_name, usage_context=usage_context) as session_id:
        chunk_extractor = _StreamingChunkExtractor()
        async for event in graph.astream(
            {"messages": [edited_message]},
            **stream_kwargs,
        ):
            stream_mode, event_data = _coerce_stream_event(event)
            if stream_mode == "custom":
                # Only context_usage custom events are forwarded to the client today.
                if isinstance(event_data, dict) and event_data.get("type") == "context_usage":
                    event_thread_id = str(event_data.get("thread_id") or "")
                    if not event_thread_id or event_thread_id == thread_id:
                        yield event_data
                    else:
                        logger.debug(
                            "Ignoring context_usage for sub-thread '%s' (main '%s')",
                            event_thread_id,
                            thread_id,
                        )
                else:
                    logger.debug("Ignoring unsupported custom stream event: %r", event_data)
                continue
            if stream_mode == "messages":
                delta = chunk_extractor.extract(event_data)
                if delta.text:
                    registry.update_step(session_id, SESSION_STEP_RESPONDING)
                    yield {
                        "type": "message",
                        "content": delta.text,
                    }
                if emit_thinking and delta.thinking:
                    yield {
                        "type": "thinking",
                        "content": delta.thinking,
                    }
                continue

            if stream_mode != "values":
                continue

            event = event_data
            for plan_review_event in _plan_review_events_from_value_event(event):
                yield plan_review_event
            for user_input_event in _user_input_request_events_from_value_event(event):
                yield user_input_event

            messages = event.get("messages", [])
            if not messages:
                continue

            current_user_index = next(
                (
                    index
                    for index, message in enumerate(messages)
                    if _message_id(message) == user_message_id
                ),
                None,
            )
            if current_user_index is not None:
                current_user_seen = True
                for message in messages[: current_user_index + 1]:
                    message_id = _message_id(message)
                    if message_id:
                        seen_ids.add(message_id)
                messages = messages[current_user_index + 1 :]
            elif not current_user_seen and len(messages) > 1:
                for message in messages:
                    message_id = _message_id(message)
                    if message_id:
                        seen_ids.add(message_id)
                continue

            for message in messages:
                message_id = _message_id(message)
                if message_id:
                    if message_id in seen_ids:
                        continue
                    seen_ids.add(message_id)

                if getattr(message, "additional_kwargs", {}).get("is_compact_summary"):
                    continue
                if isinstance(message, AIMessage):
                    tool_calls = getattr(message, "tool_calls", None)
                    content = extract_final_text_content(getattr(message, "content", None))
                    thinking = (
                        extract_thinking_content(getattr(message, "content", None))
                        if emit_thinking
                        else ""
                    )
                    if content:
                        registry.update_step(session_id, SESSION_STEP_RESPONDING)
                        yield {
                            "type": "final",
                            "content": content,
                        }
                    if thinking:
                        yield {
                            "type": "thinking",
                            "content": thinking,
                        }
                    if tool_calls:
                        for tc in tool_calls:
                            tool_name = tc.get("name") or "unknown"
                            if tool_name in {PLAN_MODE_TOOL_NAME, ASK_USER_TOOL_NAME}:
                                continue
                            registry.add_tool_call(session_id, tool_name)
                            yield {
                                "type": "tool_call",
                                "id": tc.get("id"),
                                "name": tool_name,
                                "args": tc.get("args"),
                            }
                elif isinstance(message, ToolMessage):
                    yield {
                        "type": "tool_result",
                        "tool_call_id": getattr(message, "tool_call_id", None),
                        "name": getattr(message, "name", None),
                        "content": extract_tool_display_content(message),
                    }

async def run_agent_full(
    user_input: str,
    thread_id: str,
    agent_name: str = "default",
    *,
    workflow: str | None = None,
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    working_dir: str | None = None,
    context_compact_threshold: int | None = None,
    channel_context_trim_threshold: int | None = None,
    allowed_tool_names: list[str] | None = None,
    allowed_skill_names: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    attachments: list[Any] | None = None,
    usage_context: dict[str, Any] | None = None,
    resume: bool = False,
    resume_payload: dict[str, Any] | None = None,
    checkpoint_id: str | None = None,
) -> str:
    """Run a workflow graph and return the final assistant response.

    Loads full config from agent_name, allows selective overrides.

    Note: Session tracking is handled by run_agent() internally,
    so this function does not need its own register/unregister.

    Args:
        user_input: User message
        thread_id: Session thread ID
        agent_name: Agent to use (loads config from catalog)
        workflow: Override agent's graph_type if needed
        workspace: Workspace reference for tools
        context_compact_threshold: Override agent's context_compact_threshold if needed
        channel_context_trim_threshold: Optional channel conversation token budget
        allowed_tool_names: Override agent's tools if needed
        provider: Override agent card provider for this run if needed
        model: Override agent card model for this run if needed
        attachments: Optional files attached to the user message
    """
    chunks = []
    async for chunk in run_agent(
        user_input=user_input,
        thread_id=thread_id,
        agent_name=agent_name,
        workflow=workflow,
        workspace=workspace,
        working_dir=working_dir,
        context_compact_threshold=context_compact_threshold,
        channel_context_trim_threshold=channel_context_trim_threshold,
        allowed_tool_names=allowed_tool_names,
        allowed_skill_names=allowed_skill_names,
        provider=provider,
        model=model,
        reasoning_effort=reasoning_effort,
        attachments=attachments,
        usage_context=usage_context,
        resume=resume,
        resume_payload=resume_payload,
        checkpoint_id=checkpoint_id,
    ):
        chunks.append(chunk)
    return chunks[-1] if chunks else ""
