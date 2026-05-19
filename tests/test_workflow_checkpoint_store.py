import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from agent.modules.workflows import delete_workflow_thread_tree
from agent.modules.workflows.checkpoint import (
    close_checkpointer,
    get_checkpointer as get_canonical_checkpointer,
    initialize_checkpointer,
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
async def test_delete_workflow_thread_tree_deletes_parent_and_sub_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.modules.workflows.checkpoint.store as store_module

    class FakeCheckpointer:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        async def alist(self, config):
            assert config is None
            thread_ids = [
                "parent-thread",
                "parent-thread:sub:child-agent:11111111",
                "parent-thread:sub:child-agent:22222222",
                "parent-thread:sub:child-agent:11111111",
                "parent-thread-other:sub:child-agent:33333333",
                "parent-thread_sub_child-agent_44444444",
                "parent-thread:submarine:child-agent:55555555",
            ]
            for thread_id in thread_ids:
                yield SimpleNamespace(
                    config={"configurable": {"thread_id": thread_id}}
                )

        async def adelete_thread(self, thread_id: str) -> None:
            self.deleted.append(thread_id)

    checkpointer = FakeCheckpointer()
    monkeypatch.setattr(store_module, "get_checkpointer", lambda: checkpointer)

    await delete_workflow_thread_tree("parent-thread")

    assert checkpointer.deleted == [
        "parent-thread:sub:child-agent:11111111",
        "parent-thread:sub:child-agent:22222222",
        "parent-thread",
    ]


@pytest.mark.asyncio
async def test_delete_workflow_thread_tree_deletes_parent_when_listing_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent.modules.workflows.checkpoint.store as store_module

    class FakeCheckpointer:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        async def alist(self, config):
            raise RuntimeError("list failed")
            yield

        async def adelete_thread(self, thread_id: str) -> None:
            self.deleted.append(thread_id)

    checkpointer = FakeCheckpointer()
    monkeypatch.setattr(store_module, "get_checkpointer", lambda: checkpointer)

    await delete_workflow_thread_tree("parent-thread")

    assert checkpointer.deleted == ["parent-thread"]

