import uuid

import pytest
import pytest_asyncio

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from agent.persistence import close_persistence, get_checkpointer, initialize_persistence


class PersistState(MessagesState):
    pass


def _build_test_graph():
    def reply_node(state: PersistState):
        last = state["messages"][-1]
        return {"messages": [AIMessage(content=f"echo:{last.content}")]}

    graph = StateGraph(PersistState)
    graph.add_node("reply", reply_node)
    graph.add_edge(START, "reply")
    graph.add_edge("reply", END)
    return graph.compile(checkpointer=get_checkpointer())


def _thread_config(thread_id: str):
    return {"configurable": {"thread_id": thread_id}}


@pytest_asyncio.fixture
async def setup_persistence(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    pytest.importorskip("aiosqlite")

    await initialize_persistence()
    try:
        yield
    finally:
        await close_persistence()


@pytest.mark.asyncio
async def test_same_thread_restores_conversation(setup_persistence):
    graph = _build_test_graph()
    thread_id = f"thread-{uuid.uuid4()}"
    config = _thread_config(thread_id)

    first = await graph.ainvoke(
        {"messages": [HumanMessage(content="first")]},
        config=config,
    )
    second = await graph.ainvoke(
        {"messages": [HumanMessage(content="second")]},
        config=config,
    )

    first_contents = [m.content for m in first["messages"]]
    second_contents = [m.content for m in second["messages"]]

    assert "first" in first_contents
    assert "echo:first" in first_contents
    assert "first" in second_contents
    assert "echo:first" in second_contents
    assert "second" in second_contents
    assert "echo:second" in second_contents


@pytest.mark.asyncio
async def test_different_threads_are_isolated(setup_persistence):
    graph = _build_test_graph()

    thread_a = _thread_config(f"thread-a-{uuid.uuid4()}")
    thread_b = _thread_config(f"thread-b-{uuid.uuid4()}")

    await graph.ainvoke({"messages": [HumanMessage(content="alpha")]}, config=thread_a)
    out_b = await graph.ainvoke(
        {"messages": [HumanMessage(content="beta")]},
        config=thread_b,
    )

    thread_b_contents = [m.content for m in out_b["messages"]]

    assert "alpha" not in thread_b_contents
    assert "echo:alpha" not in thread_b_contents
    assert "beta" in thread_b_contents
    assert "echo:beta" in thread_b_contents
