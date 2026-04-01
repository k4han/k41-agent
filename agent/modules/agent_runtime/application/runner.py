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
    service_type: str = "default",
    working_dir: str | None = None,
    max_context_tokens: int = 50_000,
    channel_id: str = "",
    agent_name: str | None = None,
) -> dict[str, Any]:
    """Build run parameters, resolving workflow from agent config when provided.

    Resolution priority:
    1. `agent_name` (if provided) -> load config from catalog and derive workflow.
    2. explicit `workflow`.
    3. fallback to "react_agent".
    """
    resolved_workflow = workflow
    resolved_service_type = service_type
    resolved_max_context_tokens = max_context_tokens
    resolved_agent_name = "default"

    if agent_name:
        from agent.modules.agents.public import get_catalog_service

        catalog = get_catalog_service()
        agent_config = catalog.get_agent(agent_name)
        if agent_config is None:
            raise ValueError(f"Agent '{agent_name}' not found in catalog")

        resolved_agent_name = agent_config.name
        resolved_workflow = agent_config.graph_type
        resolved_service_type = agent_config.service_type
        resolved_max_context_tokens = agent_config.max_context_tokens
    elif not resolved_workflow:
        resolved_workflow = "react_agent"

    return {
        "workflow": resolved_workflow,
        "user_input": user_input,
        "service_type": resolved_service_type,
        "working_dir": working_dir,
        "max_context_tokens": resolved_max_context_tokens,
        "thread_id": SessionManager.make_thread_id(platform, user_id, channel_id),
        "agent_name": resolved_agent_name if agent_name else "default",
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


async def run_agent(
    workflow: str,
    user_input: str,
    thread_id: str,
    service_type: str = "default",
    working_dir: str | None = None,
    max_context_tokens: int = 50_000,
    agent_name: str = "default",
) -> AsyncGenerator[str, None]:
    """Run a workflow graph and stream assistant chunks."""

    graph = get_workflow_graph(workflow)
    config = make_run_config(thread_id=thread_id)

    # Resolve allowed_tool_names from agent config
    allowed_tool_names = None
    if agent_name:
        from agent.modules.agents.public import get_catalog_service

        catalog = get_catalog_service()
        agent_config = catalog.get_agent(agent_name)
        if agent_config:
            allowed_tool_names = agent_config.tools or None

    context = make_run_context(
        service_type=service_type,
        working_dir=working_dir,
        max_context_tokens=max_context_tokens,
        agent_name=agent_name,
        allowed_tool_names=allowed_tool_names,
    )

    async for event in graph.astream(
        {"messages": [HumanMessage(content=user_input)]},
        config=config,
        context=context,
        stream_mode="values",
    ):
        messages = event.get("messages", [])
        if messages:
            last = messages[-1]
            if last.__class__.__name__ == "AIMessage" and last.content:
                yield str(last.content)


async def run_agent_stream(
    workflow: str,
    user_input: str,
    thread_id: str,
    service_type: str = "default",
    working_dir: str | None = None,
    max_context_tokens: int = 50_000,
    agent_name: str = "default",
) -> AsyncGenerator[dict[str, Any], None]:
    """Run a workflow graph and stream UI events (tool calls and text chunks)."""

    graph = get_workflow_graph(workflow)
    config = make_run_config(thread_id=thread_id)

    allowed_tool_names = None
    if agent_name:
        from agent.modules.agents.public import get_catalog_service

        catalog = get_catalog_service()
        agent_config = catalog.get_agent(agent_name)
        if agent_config:
            allowed_tool_names = agent_config.tools or None

    context = make_run_context(
        service_type=service_type,
        working_dir=working_dir,
        max_context_tokens=max_context_tokens,
        agent_name=agent_name,
        allowed_tool_names=allowed_tool_names,
    )

    seen_ids = set()

    async for event in graph.astream(
        {"messages": [HumanMessage(content=user_input)]},
        config=config,
        context=context,
        stream_mode="values",
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
    workflow: str,
    user_input: str,
    thread_id: str,
    service_type: str = "default",
    working_dir: str | None = None,
    max_context_tokens: int = 50_000,
    agent_name: str = "default",
) -> str:
    """Run a workflow graph and return the final assistant response."""

    chunks = []
    async for chunk in run_agent(
        workflow=workflow,
        user_input=user_input,
        thread_id=thread_id,
        service_type=service_type,
        working_dir=working_dir,
        max_context_tokens=max_context_tokens,
        agent_name=agent_name,
    ):
        chunks.append(chunk)
    return chunks[-1] if chunks else ""
