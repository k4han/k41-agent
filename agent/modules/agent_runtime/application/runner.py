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
    workflow: str = "react_agent",
    service_type: str = "default",
    working_dir: str | None = None,
    max_context_tokens: int = 50_000,
    channel_id: str = "",
) -> dict[str, Any]:
    return {
        "workflow": workflow,
        "user_input": user_input,
        "service_type": service_type,
        "working_dir": working_dir,
        "max_context_tokens": max_context_tokens,
        "thread_id": SessionManager.make_thread_id(platform, user_id, channel_id),
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
) -> AsyncGenerator[str, None]:
    """Run a workflow graph and stream assistant chunks."""

    graph = get_workflow_graph(workflow)
    config = make_run_config(thread_id=thread_id)
    context = make_run_context(
        service_type=service_type,
        working_dir=working_dir,
        max_context_tokens=max_context_tokens,
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
) -> AsyncGenerator[dict[str, Any], None]:
    """Run a workflow graph and stream UI events (tool calls and text chunks)."""

    graph = get_workflow_graph(workflow)
    config = make_run_config(thread_id=thread_id)
    context = make_run_context(
        service_type=service_type,
        working_dir=working_dir,
        max_context_tokens=max_context_tokens,
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
    ):
        chunks.append(chunk)
    return chunks[-1] if chunks else ""
