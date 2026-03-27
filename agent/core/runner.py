# agent/core/runner.py
# Core logic — platform agnostic (doesn't know which platform the request comes from)

from typing import AsyncGenerator
from langchain_core.messages import HumanMessage

from agent.registry import GraphRegistry
from agent.config   import make_config


async def run_agent(
    workflow:     str,
    user_input:   str,
    thread_id:    str,
    service_type: str        = "default",
    working_dir:  str | None = None,
    max_context_tokens: int  = 50_000,
) -> AsyncGenerator[str, None]:
    """
    Run graph and yield each content chunk.
    Platform-agnostic: FastAPI, Telegram, Discord all call this function.
    """
    graph  = GraphRegistry.get(workflow)
    config = make_config(
        thread_id=thread_id,
        service_type=service_type,
        working_dir=working_dir,
        max_context_tokens=max_context_tokens,
    )

    async for event in graph.astream(
        {"messages": [HumanMessage(content=user_input)]},
        config=config,
        stream_mode="values",
    ):
        messages = event.get("messages", [])
        if messages:
            last = messages[-1]
            # Only yield AI message (don't yield back HumanMessage)
            if last.__class__.__name__ == "AIMessage" and last.content:
                yield str(last.content)


async def run_agent_full(
    workflow:     str,
    user_input:   str,
    thread_id:    str,
    service_type: str        = "default",
    working_dir:  str | None = None,
    max_context_tokens: int  = 50_000,
) -> str:
    """
    Run graph and return full response (not streaming).
    Used for Telegram, Discord — platforms that don't support streaming.
    """
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
