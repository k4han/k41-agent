import base64
import binascii
from contextlib import contextmanager
import logging
from typing import Any, AsyncGenerator, Iterator
from uuid import uuid4

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.types import Command
from agent.shared.infrastructure.parsing import extract_final_text_content

from agent.modules.agent_runtime.active_sessions import (
    ActiveSession,
    SESSION_STEP_RESPONDING,
    SESSION_STEP_THINKING,
    get_active_session_registry,
    current_session_id_var,
    current_thread_id_var,
)
from agent.modules.agent_runtime.session import SessionManager
from agent.modules.workflows import (
    get_workflow_graph,
    make_run_config,
    make_run_context,
)
from agent.modules.usage import attach_usage_context, build_usage_context
from agent.modules.tools import (
    PLAN_MODE_TOOL_NAME,
    PLAN_REVIEW_INTERRUPT_TYPE,
    PlanModeResumePayload,
)
from agent.modules.workspaces import WorkspaceRef

logger = logging.getLogger(__name__)

MAX_CHAT_ATTACHMENTS = 5
MAX_TEXT_ATTACHMENT_BYTES = 100 * 1024
MAX_IMAGE_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 8 * 1024 * 1024
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
    context_trim_threshold: int | None = None,
    max_context_tokens: int | None = None,  # Backward compatibility
    channel_id: str = "",
    agent_name: str = "default",
    provider: str | None = None,
    model: str | None = None,
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
        context_trim_threshold: Override agent's context_trim_threshold if needed
        max_context_tokens: Override agent's context_trim_threshold if needed (legacy)
        channel_id: Channel identifier (for multi-channel platforms)
        agent_name: Agent to use (loads config from catalog)
        provider: Override agent card provider for this run if needed
        model: Override agent card model for this run if needed
        attachments: Optional files attached to the user message
        resume: Request to resume execution from the last checkpoint
    """
    if resume_payload is not None:
        resume = True
    params: dict[str, Any] = {
        "user_input": user_input,
        "thread_id": thread_id or SessionManager.make_thread_id(platform, user_id, channel_id),
        "agent_name": agent_name,
        "workflow": workflow,
        "workspace": workspace if workspace is not None else working_dir,
        "context_trim_threshold": context_trim_threshold,
        "max_context_tokens": max_context_tokens,
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
        if kind not in {"text", "image"}:
            raise ValueError("Only text and image attachments are supported.")

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
    return [
        {
            "name": attachment["name"],
            "mime_type": attachment["mime_type"],
            "size": attachment["size"],
            "kind": attachment["kind"],
        }
        for attachment in attachments
    ]


def _text_attachment_block(attachment: dict[str, Any]) -> dict[str, str]:
    text = (
        f"Attached text file: {attachment['name']}\n"
        f"MIME type: {attachment['mime_type']}\n"
        f"Size: {attachment['size']} bytes\n\n"
        f"{attachment['content']}"
    )
    return {"type": "text", "text": text}


def _image_metadata_block(attachment: dict[str, Any]) -> dict[str, str]:
    text = (
        f"Attached image: {attachment['name']}\n"
        f"MIME type: {attachment['mime_type']}\n"
        f"Size: {attachment['size']} bytes"
    )
    return {"type": "text", "text": text}


def _make_user_message(
    user_input: str,
    attachments: list[Any] | None = None,
) -> HumanMessage:
    message_id = f"user-{uuid4()}"
    normalized_attachments = _normalize_chat_attachments(attachments)
    if not normalized_attachments:
        return HumanMessage(content=user_input, id=message_id)

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
    title: str = "",
    attachments: list[Any] | None = None,
) -> None:
    try:
        from agent.modules.conversations import (
            THREAD_KIND_USER,
            get_conversation_thread,
            infer_thread_kind,
            schedule_conversation_title_generation,
            upsert_conversation_thread,
        )

        kind = infer_thread_kind(thread_id)
        resolved_title = title
        should_generate_title = False
        if kind == THREAD_KIND_USER:
            existing = await get_conversation_thread(thread_id)
            existing_title = str((existing or {}).get("title") or "").strip()
            if existing_title and existing_title != thread_id:
                resolved_title = ""
            else:
                should_generate_title = True

        await upsert_conversation_thread(
            thread_id=thread_id,
            agent_name=agent_name,
            title=resolved_title,
            kind=kind,
        )
        if should_generate_title:
            schedule_conversation_title_generation(
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


def _coerce_stream_event(event: Any) -> tuple[str, Any]:
    if isinstance(event, tuple):
        if len(event) == 2 and isinstance(event[0], str):
            return event[0], event[1]
        if len(event) == 3 and isinstance(event[1], str):
            return event[1], event[2]
    return "values", event


def _extract_message_chunk_content(event: Any) -> str:
    chunk = event[0] if isinstance(event, tuple) and event else event
    if not isinstance(chunk, AIMessageChunk):
        return ""

    def extract_part(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            part_type = str(value.get("type", "") or "").strip().lower()
            if part_type == "thinking":
                return ""
            text = value.get("text")
            if isinstance(text, str):
                return text
            content = value.get("content")
            if isinstance(content, list):
                return "".join(extract_part(part) for part in content)
            if isinstance(content, str):
                return content
            return ""
        text_attr = getattr(value, "text", None)
        return text_attr if isinstance(text_attr, str) else ""

    content = getattr(chunk, "content", None)
    if isinstance(content, list):
        return "".join(extract_part(part) for part in content)
    return extract_part(content)


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


def _resolve_agent_name_for_resume(
    catalog: Any,
    agent_name: str,
    resume_payload: PlanModeResumePayload | None,
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
    resume_payload: PlanModeResumePayload | None,
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


def _find_plan_tool_call_id(messages: list[Any], fallback: str = "") -> str:
    for message in reversed(messages):
        tool_calls = getattr(message, "tool_calls", None) or []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            if tool_call.get("name") == PLAN_MODE_TOOL_NAME:
                return str(tool_call.get("id") or fallback)
    return fallback


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
        next_content: list[Any] = []
        replaced = False
        for part in content:
            if (
                not replaced
                and isinstance(part, dict)
                and str(part.get("type") or "").strip().lower() == "text"
            ):
                next_content.append({**part, "text": text})
                replaced = True
                continue
            next_content.append(part)
        if not replaced:
            next_content.insert(0, {"type": "text", "text": text})
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
def _track_active_session(thread_id: str, agent_name: str) -> Iterator[str]:
    import asyncio
    registry = get_active_session_registry()
    try:
        platform, user_id, channel_id = SessionManager.parse_thread_id(thread_id)
    except ValueError:
        platform, user_id, channel_id = "unknown", thread_id, ""
    session = ActiveSession(
        thread_id=thread_id,
        platform=platform,
        user_id=user_id,
        channel_id=channel_id,
        agent_name=agent_name,
    )
    
    current_task = None
    try:
        current_task = asyncio.current_task()
    except RuntimeError:
        pass

    session_id = registry.register(session, task=current_task)
    session_token = current_session_id_var.set(session_id)
    thread_token = current_thread_id_var.set(thread_id)
    try:
        registry.update_step(session_id, SESSION_STEP_THINKING)
        yield session_id
    finally:
        current_thread_id_var.reset(thread_token)
        current_session_id_var.reset(session_token)
        registry.unregister(session_id)


async def run_agent(
    user_input: str,
    thread_id: str,
    agent_name: str = "default",
    *,
    workflow: str | None = None,
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    working_dir: str | None = None,
    context_trim_threshold: int | None = None,
    max_context_tokens: int | None = None,  # Backward compatibility
    allowed_tool_names: list[str] | None = None,
    allowed_skill_names: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
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
        context_trim_threshold: Override agent's context_trim_threshold if needed
        max_context_tokens: Override agent's context_trim_threshold if needed (legacy)
        allowed_tool_names: Override agent's tools if needed
        provider: Override agent card provider for this run if needed
        model: Override agent card model for this run if needed
        attachments: Optional files attached to the user message
    """
    from agent.modules.agents import get_catalog_service

    catalog = get_catalog_service()
    normalized_resume_payload = _normalize_plan_resume_payload(resume_payload)
    _validate_plan_resume_payload(normalized_resume_payload)
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
    agent_config = catalog.get_agent(agent_name)
    if agent_config is None:
        raise ValueError(f"Agent '{agent_name}' not found in catalog")

    # Resolve: explicit params > agent config
    resolved_workflow = workflow or agent_config.graph_type
    config_threshold = getattr(
        agent_config, "context_trim_threshold", None
    ) or getattr(agent_config, "max_context_tokens", None)
    resolved_threshold = (
        context_trim_threshold
        if context_trim_threshold is not None
        else (max_context_tokens or config_threshold)
    )
    resolved_tools = allowed_tool_names if allowed_tool_names is not None else agent_config.tools

    graph = get_workflow_graph(resolved_workflow)
    config = attach_usage_context(
        make_run_config(thread_id=thread_id),
        build_usage_context(thread_id, usage_context),
    )
    config = _config_with_checkpoint(config, checkpoint_id)

    context = make_run_context(
        workspace=workspace,
        working_dir=working_dir,
        context_trim_threshold=resolved_threshold,
        max_context_tokens=resolved_threshold,
        agent_name=agent_name,
        allowed_tool_names=resolved_tools or None,
        allowed_skill_names=allowed_skill_names,
        provider=provider,
        model=model,
    )
    if normalized_resume_payload is not None and normalized_resume_payload.action == "approve":
        await _update_thread_agent(thread_id, agent_name)
    elif not resume and not checkpoint_id:
        await _record_conversation_thread(
            thread_id=thread_id,
            agent_name=agent_name,
            title=user_input,
            attachments=attachments,
        )

    stream_kwargs: dict[str, Any] = {
        "config": config,
        "stream_mode": "values",
    }
    if _graph_accepts_context(graph):
        stream_kwargs["context"] = context

    registry = get_active_session_registry()
    if normalized_resume_payload is not None:
        input_data = Command(resume=normalized_resume_payload.model_dump(exclude_none=True))
    elif resume:
        input_data = None
    else:
        input_data = {"messages": [_make_user_message(user_input, attachments)]}

    with _track_active_session(thread_id, agent_name) as session_id:
        async for event in graph.astream(
            input_data,
            **stream_kwargs,
        ):
            messages = event.get("messages", [])
            if messages:
                last = messages[-1]
                if isinstance(last, AIMessage):
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
    context_trim_threshold: int | None = None,
    max_context_tokens: int | None = None,  # Backward compatibility
    allowed_tool_names: list[str] | None = None,
    allowed_skill_names: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    attachments: list[Any] | None = None,
    usage_context: dict[str, Any] | None = None,
    resume: bool = False,
    resume_payload: dict[str, Any] | None = None,
    checkpoint_id: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run a workflow graph and stream UI events (tool calls and text chunks).

    Loads full config from agent_name, allows selective overrides.

    Args:
        user_input: User message
        thread_id: Session thread ID
        agent_name: Agent to use (loads config from catalog)
        workflow: Override agent's graph_type if needed
        workspace: Workspace reference for tools
        context_trim_threshold: Override agent's context_trim_threshold if needed
        max_context_tokens: Override agent's context_trim_threshold if needed (legacy)
        allowed_tool_names: Override agent's tools if needed
        provider: Override agent card provider for this run if needed
        model: Override agent card model for this run if needed
        attachments: Optional files attached to the user message
        resume: Request to resume execution from the last checkpoint
    """
    from agent.modules.agents import get_catalog_service

    catalog = get_catalog_service()
    normalized_resume_payload = _normalize_plan_resume_payload(resume_payload)
    _validate_plan_resume_payload(normalized_resume_payload)
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
    agent_config = catalog.get_agent(agent_name)
    if agent_config is None:
        raise ValueError(f"Agent '{agent_name}' not found in catalog")

    # Resolve: explicit params > agent config
    resolved_workflow = workflow or agent_config.graph_type
    config_threshold = getattr(
        agent_config, "context_trim_threshold", None
    ) or getattr(agent_config, "max_context_tokens", None)
    resolved_threshold = (
        context_trim_threshold
        if context_trim_threshold is not None
        else (max_context_tokens or config_threshold)
    )
    resolved_tools = allowed_tool_names if allowed_tool_names is not None else agent_config.tools

    graph = get_workflow_graph(resolved_workflow)
    config = attach_usage_context(
        make_run_config(thread_id=thread_id),
        build_usage_context(thread_id, usage_context),
    )
    config = _config_with_checkpoint(config, checkpoint_id)

    context = make_run_context(
        workspace=workspace,
        working_dir=working_dir,
        context_trim_threshold=resolved_threshold,
        max_context_tokens=resolved_threshold,
        agent_name=agent_name,
        allowed_tool_names=resolved_tools or None,
        allowed_skill_names=allowed_skill_names,
        provider=provider,
        model=model,
    )
    if normalized_resume_payload is not None and normalized_resume_payload.action == "approve":
        await _update_thread_agent(thread_id, agent_name)
    elif not resume and not checkpoint_id:
        await _record_conversation_thread(
            thread_id=thread_id,
            agent_name=agent_name,
            title=user_input,
            attachments=attachments,
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
        "stream_mode": ["messages", "values"],
    }
    if _graph_accepts_context(graph):
        stream_kwargs["context"] = context

    registry = get_active_session_registry()
    if resume:
        input_data = None
        user_message_id = None
        current_user_seen = True
        if normalized_resume_payload is not None:
            input_data = Command(
                resume=normalized_resume_payload.model_dump(exclude_none=True)
            )
    else:
        user_message = _make_user_message(user_input, attachments)
        input_data = {"messages": [user_message]}
        user_message_id = _message_id(user_message)
        current_user_seen = False

    with _track_active_session(thread_id, agent_name) as session_id:
        async for event in graph.astream(
            input_data,
            **stream_kwargs,
        ):
            stream_mode, event_data = _coerce_stream_event(event)
            if stream_mode == "messages":
                content = _extract_message_chunk_content(event_data)
                if content:
                    registry.update_step(session_id, SESSION_STEP_RESPONDING)
                    yield {
                        "type": "message",
                        "content": content,
                    }
                continue

            if stream_mode != "values":
                continue

            event = event_data
            for plan_review_event in _plan_review_events_from_value_event(event):
                yield plan_review_event

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

                if isinstance(message, AIMessage):
                    tool_calls = getattr(message, "tool_calls", None)
                    content = extract_final_text_content(getattr(message, "content", None))
                    if content:
                        registry.update_step(session_id, SESSION_STEP_RESPONDING)
                        yield {
                            "type": "final",
                            "content": content,
                        }
                    if tool_calls:
                        for tc in tool_calls:
                            tool_name = tc.get("name") or "unknown"
                            if tool_name == PLAN_MODE_TOOL_NAME:
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
                        "content": extract_final_text_content(getattr(message, "content", None)),
                    }


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
    context_trim_threshold: int | None = None,
    max_context_tokens: int | None = None,
    allowed_tool_names: list[str] | None = None,
    allowed_skill_names: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    usage_context: dict[str, Any] | None = None,
    resume: bool = False,
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
    config_threshold = getattr(
        agent_config, "context_trim_threshold", None
    ) or getattr(agent_config, "max_context_tokens", None)
    resolved_threshold = (
        context_trim_threshold
        if context_trim_threshold is not None
        else (max_context_tokens or config_threshold)
    )
    resolved_tools = allowed_tool_names if allowed_tool_names is not None else agent_config.tools

    graph = get_workflow_graph(resolved_workflow)
    base_config = attach_usage_context(
        make_run_config(thread_id=thread_id),
        build_usage_context(thread_id, usage_context),
    )

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
        context_trim_threshold=resolved_threshold,
        max_context_tokens=resolved_threshold,
        agent_name=agent_name,
        allowed_tool_names=resolved_tools or None,
        allowed_skill_names=allowed_skill_names,
        provider=provider,
        model=model,
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
        "stream_mode": ["messages", "values"],
    }
    if _graph_accepts_context(graph):
        stream_kwargs["context"] = context

    registry = get_active_session_registry()
    user_message_id = _message_id(edited_message)
    current_user_seen = False

    with _track_active_session(thread_id, agent_name) as session_id:
        async for event in graph.astream(
            {"messages": [edited_message]},
            **stream_kwargs,
        ):
            stream_mode, event_data = _coerce_stream_event(event)
            if stream_mode == "messages":
                content = _extract_message_chunk_content(event_data)
                if content:
                    registry.update_step(session_id, SESSION_STEP_RESPONDING)
                    yield {
                        "type": "message",
                        "content": content,
                    }
                continue

            if stream_mode != "values":
                continue

            event = event_data
            for plan_review_event in _plan_review_events_from_value_event(event):
                yield plan_review_event

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

                if isinstance(message, AIMessage):
                    tool_calls = getattr(message, "tool_calls", None)
                    content = extract_final_text_content(getattr(message, "content", None))
                    if content:
                        registry.update_step(session_id, SESSION_STEP_RESPONDING)
                        yield {
                            "type": "final",
                            "content": content,
                        }
                    if tool_calls:
                        for tc in tool_calls:
                            tool_name = tc.get("name") or "unknown"
                            if tool_name == PLAN_MODE_TOOL_NAME:
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
                        "content": extract_final_text_content(getattr(message, "content", None)),
                    }

async def run_agent_full(
    user_input: str,
    thread_id: str,
    agent_name: str = "default",
    *,
    workflow: str | None = None,
    workspace: WorkspaceRef | dict[str, Any] | str | None = None,
    working_dir: str | None = None,
    context_trim_threshold: int | None = None,
    max_context_tokens: int | None = None,  # Backward compatibility
    allowed_tool_names: list[str] | None = None,
    allowed_skill_names: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
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
        context_trim_threshold: Override agent's context_trim_threshold if needed
        max_context_tokens: Override agent's context_trim_threshold if needed (legacy)
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
        context_trim_threshold=context_trim_threshold,
        max_context_tokens=max_context_tokens,
        allowed_tool_names=allowed_tool_names,
        allowed_skill_names=allowed_skill_names,
        provider=provider,
        model=model,
        attachments=attachments,
        usage_context=usage_context,
        resume=resume,
        resume_payload=resume_payload,
        checkpoint_id=checkpoint_id,
    ):
        chunks.append(chunk)
    return chunks[-1] if chunks else ""
