# test_local.py
# Chạy thử trực tiếp, không cần FastAPI server
# Dùng: python test_local.py

import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from agent.graphs import setup_all_graphs
from agent.persistence import close_persistence, initialize_persistence
from agent.core.runner import run_agent_full, run_agent


async def test_chat():
    print("\n=== TEST: chat_agent ===")
    response = await run_agent_full(
        workflow="chat_agent",
        user_input="Bây giờ là mấy giờ?",
        thread_id="test_chat_1",
        service_type="default",
    )
    print(f"Response: {response}")


async def test_coding():
    print("\n=== TEST: coding_agent ===")
    response = await run_agent_full(
        workflow="coding_agent",
        user_input="Liệt kê các file trong thư mục hiện tại",
        thread_id="test_coding_1",
        service_type="backend",
        working_dir=os.getcwd(),
    )
    print(f"Response: {response}")


async def test_research():
    print("\n=== TEST: research_chain ===")
    response = await run_agent_full(
        workflow="research_chain",
        user_input="Phân tích ưu nhược điểm của kiến trúc microservices",
        thread_id="test_research_1",
    )
    print(f"Response: {response}")


async def test_stream():
    print("\n=== TEST: streaming (chat_agent) ===")
    async for chunk in run_agent(
        workflow="chat_agent",
        user_input="Giải thích LangGraph trong 3 câu",
        thread_id="test_stream_1",
    ):
        print(chunk, end="", flush=True)
    print()


async def test_concurrent():
    print("\n=== TEST: concurrent requests ===")
    results = await asyncio.gather(
        run_agent_full(
            workflow="chat_agent",
            user_input="Request 1: Xin chào!",
            thread_id="concurrent_1",
            service_type="default",
        ),
        run_agent_full(
            workflow="chat_agent",
            user_input="Request 2: Bạn là ai?",
            thread_id="concurrent_2",
            service_type="default",
        ),
        run_agent_full(
            workflow="coding_agent",
            user_input="Request 3: Liệt kê files",
            thread_id="concurrent_3",
            service_type="backend",
            working_dir=os.getcwd(),
        ),
    )
    for i, r in enumerate(results, 1):
        print(f"[{i}] {r[:80]}...")


async def main():
    print("Initializing persistence...")
    await initialize_persistence()

    print("Setting up graphs...")
    setup_all_graphs()

    try:
        await test_chat()
        await test_coding()
        await test_research()
        await test_stream()
        await test_concurrent()
    finally:
        await close_persistence()

    print("\n✅ All tests done.")


if __name__ == "__main__":
    asyncio.run(main())
