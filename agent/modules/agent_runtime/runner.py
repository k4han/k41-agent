from contextlib import contextmanager
import logging
from typing import Any, AsyncGenerator, Iterator

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from agent.shared.infrastructure.parsing import extract_final_text_content

from agent.modules.agent_runtime.active_sessions import (
    ActiveSession,
    SESSION_STEP_RESPONDING,
    SESSION_STEP_THINKING,
    get_active_session_registry,
)
from agent.modules.agent_runtime.session import SessionManager
from agent.modules.workflows import (
    get_workflow_graph,
    make_run_config,
    make_run_context,
)

logger = logging.getLogger(__name__)


def build_run_params(
    *,
    platform: str,
    user_id: str,
    user_input: str,
    thread_id: str | None = None,
    workflow: str | None = None,
    working_dir: str | None = None,
    max_context_tokens: int | None = None,
    channel_id: str = "",
    agent_name: str = "default",
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Build run parameters for agent execution.

    All config is loaded from agent_name, with optional overrides.

    Args:
        platform: Platform identifier (telegram, discord, api, etc.)
        user_id: User identifier
        user_input: User message
        thread_id: Existing session thread ID to resume
        workflow: Override agent's graph_type if needed
        working_dir: Working directory for tools
        max_context_tokens: Override agent's max_context_tokens if needed
        channel_id: Channel identifier (for multi-channel platforms)
        agent_name: Agent to use (loads config from catalog)
        provider: Override agent card provider for this run if needed
        model: Override agent card model for this run if needed
    """
    return {
        "user_input": user_input,
        "thread_id": thread_id or SessionManager.make_thread_id(platform, user_id, channel_id),
        "agent_name": agent_name,
        "workflow": workflow,
        "working_dir": working_dir,
        "max_context_tokens": max_context_tokens,
        "provider": provider,
        "model": model,
    }


async def clear_agent_session(
    *,
    platform: str,
    user_id: str,
    channel_id: str = "",
) -> None:
    """Clear the session history (checkpoint thread) for a specific user and channel."""
    from agent.modules.workflows import delete_workflow_thread

    thread_id = SessionManager.make_thread_id(platform, user_id, channel_id)
    await delete_workflow_thread(thread_id)


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
) -> None:
    try:
        from agent.modules.conversations import upsert_conversation_thread

        await upsert_conversation_thread(
            thread_id=thread_id,
            agent_name=agent_name,
            title=title,
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


@contextmanager
def _track_active_session(thread_id: str, agent_name: str) -> Iterator[str]:
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
    session_id = registry.register(session)
    try:
        registry.update_step(session_id, SESSION_STEP_THINKING)
        yield session_id
    finally:
        registry.unregister(session_id)


async def run_agent(
    user_input: str,
    thread_id: str,
    agent_name: str = "default",
    *,
    workflow: str | None = None,
    working_dir: str | None = None,
    max_context_tokens: int | None = None,
    allowed_tool_names: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> AsyncGenerator[str, None]:
    """Run a workflow graph and stream assistant chunks.

    Loads full config from agent_name, allows selective overrides.

    Args:
        user_input: User message
        thread_id: Session thread ID
        agent_name: Agent to use (loads config from catalog)
        workflow: Override agent's graph_type if needed
        working_dir: Working directory for tools
        max_context_tokens: Override agent's max_context_tokens if needed
        allowed_tool_names: Override agent's tools if needed
        provider: Override agent card provider for this run if needed
        model: Override agent card model for this run if needed
    """
    from agent.modules.agents import get_catalog_service

    catalog = get_catalog_service()
    agent_config = catalog.get_agent(agent_name)
    if agent_config is None:
        raise ValueError(f"Agent '{agent_name}' not found in catalog")

    # Resolve: explicit params > agent config
    resolved_workflow = workflow or agent_config.graph_type
    resolved_max_tokens = max_context_tokens or agent_config.max_context_tokens
    resolved_tools = allowed_tool_names if allowed_tool_names is not None else agent_config.tools

    graph = get_workflow_graph(resolved_workflow)
    config = make_run_config(thread_id=thread_id)

    context = make_run_context(
        working_dir=working_dir,
        max_context_tokens=resolved_max_tokens,
        agent_name=agent_name,
        allowed_tool_names=resolved_tools or None,
        provider=provider,
        model=model,
    )
    await _record_conversation_thread(
        thread_id=thread_id,
        agent_name=agent_name,
        title=user_input,
    )

    stream_kwargs: dict[str, Any] = {
        "config": config,
        "stream_mode": "values",
    }
    if _graph_accepts_context(graph):
        stream_kwargs["context"] = context

    registry = get_active_session_registry()
    with _track_active_session(thread_id, agent_name) as session_id:
        async for event in graph.astream(
            {"messages": [HumanMessage(content=user_input)]},
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
    working_dir: str | None = None,
    max_context_tokens: int | None = None,
    allowed_tool_names: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run a workflow graph and stream UI events (tool calls and text chunks).

    Loads full config from agent_name, allows selective overrides.

    Args:
        user_input: User message
        thread_id: Session thread ID
        agent_name: Agent to use (loads config from catalog)
        workflow: Override agent's graph_type if needed
        working_dir: Working directory for tools
        max_context_tokens: Override agent's max_context_tokens if needed
        allowed_tool_names: Override agent's tools if needed
        provider: Override agent card provider for this run if needed
        model: Override agent card model for this run if needed
    """
    from agent.modules.agents import get_catalog_service

    catalog = get_catalog_service()
    agent_config = catalog.get_agent(agent_name)
    if agent_config is None:
        raise ValueError(f"Agent '{agent_name}' not found in catalog")

    # Resolve: explicit params > agent config
    resolved_workflow = workflow or agent_config.graph_type
    resolved_max_tokens = max_context_tokens or agent_config.max_context_tokens
    resolved_tools = allowed_tool_names if allowed_tool_names is not None else agent_config.tools

    graph = get_workflow_graph(resolved_workflow)
    config = make_run_config(thread_id=thread_id)

    context = make_run_context(
        working_dir=working_dir,
        max_context_tokens=resolved_max_tokens,
        agent_name=agent_name,
        allowed_tool_names=resolved_tools or None,
        provider=provider,
        model=model,
    )
    await _record_conversation_thread(
        thread_id=thread_id,
        agent_name=agent_name,
        title=user_input,
    )

    seen_ids = set()

    stream_kwargs: dict[str, Any] = {
        "config": config,
        "stream_mode": ["messages", "values"],
    }
    if _graph_accepts_context(graph):
        stream_kwargs["context"] = context

    registry = get_active_session_registry()
    with _track_active_session(thread_id, agent_name) as session_id:
        async for event in graph.astream(
            {"messages": [HumanMessage(content=user_input)]},
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
            messages = event.get("messages", [])
            if not messages:
                continue

            last = messages[-1]
            message_id = getattr(last, "id", None)
            if message_id:
                if message_id in seen_ids:
                    continue
                seen_ids.add(message_id)

            if isinstance(last, AIMessage):
                tool_calls = getattr(last, "tool_calls", None)
                content = extract_final_text_content(getattr(last, "content", None))
                if content:
                    registry.update_step(session_id, SESSION_STEP_RESPONDING)
                    yield {
                        "type": "final",
                        "content": content,
                    }
                if tool_calls:
                    for tc in tool_calls:
                        tool_name = tc.get("name") or "unknown"
                        registry.add_tool_call(session_id, tool_name)
                        yield {
                            "type": "tool_call",
                            "id": tc.get("id"),
                            "name": tool_name,
                            "args": tc.get("args"),
                        }
            elif isinstance(last, ToolMessage):
                yield {
                    "type": "tool_result",
                    "tool_call_id": getattr(last, "tool_call_id", None),
                    "name": getattr(last, "name", None),
                    "content": extract_final_text_content(getattr(last, "content", None)),
                }

async def run_agent_full(
    user_input: str,
    thread_id: str,
    agent_name: str = "default",
    *,
    workflow: str | None = None,
    working_dir: str | None = None,
    max_context_tokens: int | None = None,
    allowed_tool_names: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
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
        working_dir: Working directory for tools
        max_context_tokens: Override agent's max_context_tokens if needed
        allowed_tool_names: Override agent's tools if needed
        provider: Override agent card provider for this run if needed
        model: Override agent card model for this run if needed
    """
    chunks = []
    async for chunk in run_agent(
        user_input=user_input,
        thread_id=thread_id,
        agent_name=agent_name,
        workflow=workflow,
        working_dir=working_dir,
        max_context_tokens=max_context_tokens,
        allowed_tool_names=allowed_tool_names,
        provider=provider,
        model=model,
    ):
        chunks.append(chunk)
    return chunks[-1] if chunks else ""
