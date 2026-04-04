from typing import Any, AsyncGenerator

from langchain_core.messages import HumanMessage

from agent.modules.agent_runtime.application.session import SessionManager
from agent.modules.workflows.public import (
    get_workflow_graph,
    make_run_config,
    make_run_context,
)


def build_run_params(
    *,
    platform: str,
    user_id: str,
    user_input: str,
    workflow: str | None = None,
    working_dir: str | None = None,
    max_context_tokens: int | None = None,
    channel_id: str = "",
    agent_name: str = "default",
) -> dict[str, Any]:
    """Build run parameters for agent execution.

    All config is loaded from agent_name, with optional overrides.

    Args:
        platform: Platform identifier (telegram, discord, api, etc.)
        user_id: User identifier
        user_input: User message
        workflow: Override agent's graph_type if needed
        working_dir: Working directory for tools
        max_context_tokens: Override agent's max_context_tokens if needed
        channel_id: Channel identifier (for multi-channel platforms)
        agent_name: Agent to use (loads config from catalog)
    """
    return {
        "user_input": user_input,
        "thread_id": SessionManager.make_thread_id(platform, user_id, channel_id),
        "agent_name": agent_name,
        "workflow": workflow,
        "working_dir": working_dir,
        "max_context_tokens": max_context_tokens,
    }


async def clear_agent_session(
    *,
    platform: str,
    user_id: str,
    channel_id: str = "",
) -> None:
    """Clear the session history (checkpoint thread) for a specific user and channel."""
    from agent.modules.workflows.public import delete_workflow_thread

    thread_id = SessionManager.make_thread_id(platform, user_id, channel_id)
    await delete_workflow_thread(thread_id)


def _graph_accepts_context(graph: Any) -> bool:
    context_schema = getattr(graph, "context_schema", Ellipsis)
    if context_schema is Ellipsis:
        return True
    return context_schema is not None


async def run_agent(
    user_input: str,
    thread_id: str,
    agent_name: str = "default",
    *,
    workflow: str | None = None,
    working_dir: str | None = None,
    max_context_tokens: int | None = None,
    allowed_tool_names: list[str] | None = None,
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
    """
    from agent.modules.agents.public import get_catalog_service

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
    )

    stream_kwargs: dict[str, Any] = {
        "config": config,
        "stream_mode": "values",
    }
    if _graph_accepts_context(graph):
        stream_kwargs["context"] = context

    async for event in graph.astream(
        {"messages": [HumanMessage(content=user_input)]},
        **stream_kwargs,
    ):
        messages = event.get("messages", [])
        if messages:
            last = messages[-1]
            if last.__class__.__name__ == "AIMessage" and last.content:
                yield str(last.content)


async def run_agent_stream(
    user_input: str,
    thread_id: str,
    agent_name: str = "default",
    *,
    workflow: str | None = None,
    working_dir: str | None = None,
    max_context_tokens: int | None = None,
    allowed_tool_names: list[str] | None = None,
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
    """
    from agent.modules.agents.public import get_catalog_service

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
    )

    seen_ids = set()

    stream_kwargs: dict[str, Any] = {
        "config": config,
        "stream_mode": "values",
    }
    if _graph_accepts_context(graph):
        stream_kwargs["context"] = context

    async for event in graph.astream(
        {"messages": [HumanMessage(content=user_input)]},
        **stream_kwargs,
    ):
        messages = event.get("messages", [])
        if not messages:
            continue

        last = messages[-1]
        if last.id in seen_ids:
            continue
        seen_ids.add(last.id)

        if last.__class__.__name__ == "AIMessage":
            tool_calls = getattr(last, "tool_calls", None)
            if tool_calls:
                for tc in tool_calls:
                    yield {
                        "type": "tool_call",
                        "name": tc.get("name"),
                        "args": tc.get("args")
                    }
            if last.content and not tool_calls:
                yield {
                    "type": "final",
                    "content": str(last.content)
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
) -> str:
    """Run a workflow graph and return the final assistant response.

    Loads full config from agent_name, allows selective overrides.

    Args:
        user_input: User message
        thread_id: Session thread ID
        agent_name: Agent to use (loads config from catalog)
        workflow: Override agent's graph_type if needed
        working_dir: Working directory for tools
        max_context_tokens: Override agent's max_context_tokens if needed
        allowed_tool_names: Override agent's tools if needed
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
    ):
        chunks.append(chunk)
    return chunks[-1] if chunks else ""
