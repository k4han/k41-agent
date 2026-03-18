# agent/core/runner.py
# Core logic — không biết request đến từ platform nào

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
) -> AsyncGenerator[str, None]:
    """
    Chạy graph và yield từng chunk nội dung.
    Platform-agnostic: FastAPI, Telegram, Discord đều gọi hàm này.
    """
    graph  = GraphRegistry.get(workflow)
    config = make_config(
        thread_id=thread_id,
        service_type=service_type,
        working_dir=working_dir,
    )

    async for event in graph.astream(
        {"messages": [HumanMessage(content=user_input)]},
        config=config,
        stream_mode="values",
    ):
        messages = event.get("messages", [])
        if messages:
            last = messages[-1]
            # Chỉ yield message của AI (không yield lại HumanMessage)
            if last.__class__.__name__ == "AIMessage" and last.content:
                yield str(last.content)


async def run_agent_full(
    workflow:     str,
    user_input:   str,
    thread_id:    str,
    service_type: str        = "default",
    working_dir:  str | None = None,
) -> str:
    """
    Chạy graph và trả về toàn bộ response (không stream).
    Dùng cho Telegram, Discord — những platform không support streaming.
    """
    chunks = []
    async for chunk in run_agent(
        workflow=workflow,
        user_input=user_input,
        thread_id=thread_id,
        service_type=service_type,
        working_dir=working_dir,
    ):
        chunks.append(chunk)
    return chunks[-1] if chunks else ""
