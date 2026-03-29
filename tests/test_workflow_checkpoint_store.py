import uuid

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from agent.modules.workflows.infrastructure.langgraph.checkpoint import (
    close_checkpointer,
    get_checkpointer as get_canonical_checkpointer,
    initialize_checkpointer,
)
from agent.persistence import (
    close_persistence,
    get_checkpointer as get_legacy_checkpointer,
    initialize_persistence,
)


class PersistState(MessagesState):
    pass


def _build_test_graph(checkpointer):
    def reply_node(state: PersistState):
        last = state["messages"][-1]
        return {"messages": [AIMessage(content=f"echo:{last.content}")]}

    graph = StateGraph(PersistState)
    graph.add_node("reply", reply_node)
    graph.add_edge(START, "reply")
    graph.add_edge("reply", END)
    return graph.compile(checkpointer=checkpointer)


def _thread_config(thread_id: str):
    return {"configurable": {"thread_id": thread_id}}


@pytest_asyncio.fixture
async def canonical_checkpointer_setup(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    pytest.importorskip("aiosqlite")

    await initialize_checkpointer()
    try:
        yield
    finally:
        await close_checkpointer()


@pytest.mark.asyncio
async def test_canonical_checkpointer_restores_conversation(canonical_checkpointer_setup):
    graph = _build_test_graph(get_canonical_checkpointer())
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

    first_contents = [message.content for message in first["messages"]]
    second_contents = [message.content for message in second["messages"]]

    assert "first" in first_contents
    assert "echo:first" in first_contents
    assert "first" in second_contents
    assert "echo:first" in second_contents
    assert "second" in second_contents
    assert "echo:second" in second_contents


@pytest.mark.asyncio
async def test_legacy_persistence_checkpointer_matches_canonical(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    pytest.importorskip("aiosqlite")

    await initialize_persistence()
    try:
        assert get_legacy_checkpointer() is get_canonical_checkpointer()
    finally:
        await close_persistence()
