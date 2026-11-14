from copy import copy
from importlib import import_module

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import START

from agent.modules.workflows.registry import GraphRegistry


@pytest.fixture(
    params=[
        ("react_agent", "build_react_graph", "react_agent", ("llm",)),
        (
            "decision_agent",
            "build_decision_agent_graph",
            "decision_agent",
            ("decision", "llm"),
        ),
        ("research", "build_research_graph", "research_chain", ("research", "summarize")),
    ],
    ids=["react", "decision", "research"],
)
def workflow_graph(request, monkeypatch):
    module_name, builder_name, graph_name, node_names = request.param
    module = import_module(f"agent.modules.workflows.graphs.{module_name}")
    first_node_history = []
    node_attributes = {
        "llm": "llm_node",
        "decision": "decision_node",
        "research": "_research_node",
        "summarize": "_summarize_node",
    }

    def make_node(name):
        def run(state):
            if name == node_names[0]:
                first_node_history.append(list(state["messages"]))
            if name == "decision":
                return {}
            return {"messages": [AIMessage(content=f"{name}:reply")]}

        return run

    for name in node_names:
        monkeypatch.setattr(module, node_attributes[name], make_node(name))
    monkeypatch.setattr(GraphRegistry, "_graphs", {})
    monkeypatch.setattr(GraphRegistry, "_descriptions", {})
    monkeypatch.setattr(GraphRegistry, "_routeable", set())

    def build(checkpointer):
        monkeypatch.setattr(module, "get_checkpointer", lambda: checkpointer)
        getattr(module, builder_name)()
        return GraphRegistry.get(graph_name)

    return build, node_names, first_node_history


@pytest.mark.asyncio
async def test_new_turns_skip_empty_entry_checkpoint(workflow_graph):
    build, node_names, first_node_history = workflow_graph
    config = {"configurable": {"thread_id": "direct-entry"}}

    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        graph = build(saver)
        previous_messages = []
        for turn in range(1, 3):
            message = HumanMessage(content="x" * 100000, id=f"human-{turn}")
            updates = [
                update
                async for update in graph.astream({"messages": [message]}, config=config)
            ]
            checkpoints = [checkpoint async for checkpoint in saver.alist(config)]

            # Each turn needs input, START, and one checkpoint per useful node.
            assert len(checkpoints) == turn * (2 + len(node_names))
            assert [name for update in updates for name in update] == list(node_names)
            expected_history = [*previous_messages, message]
            assert first_node_history[-1] == expected_history
            snapshot = await graph.aget_state(config)
            previous_messages = snapshot.values["messages"]
            assert previous_messages[: len(expected_history)] == expected_history
            assert snapshot.next == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("pause", ["before", "after"])
async def test_legacy_entry_checkpoints_still_resume(workflow_graph, pause):
    build, node_names, first_node_history = workflow_graph
    config = {"configurable": {"thread_id": "legacy-entry"}}
    message = HumanMessage(content="original request", id="human-1")

    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        graph = build(saver)
        legacy_builder = copy(graph.builder)
        legacy_builder.edges = {
            edge for edge in graph.builder.edges if edge[0] != START
        } | {(START, "prepare_context")}
        legacy = legacy_builder.compile(
            checkpointer=saver,
            interrupt_before=["prepare_context"] if pause == "before" else None,
            interrupt_after=["prepare_context"] if pause == "after" else None,
        )
        await legacy.ainvoke({"messages": [message]}, config=config)
        paused = await legacy.aget_state(config)
        assert paused.next == ("prepare_context" if pause == "before" else node_names[0],)
        assert first_node_history == []

        result = await graph.ainvoke(None, config=config)

        assert first_node_history == [[message]]
        assert result["messages"][0] == message
        assert result["messages"][-1].content == f"{node_names[-1]}:reply"
        assert (await graph.aget_state(config)).next == ()

        next_message = HumanMessage(content="follow-up", id="human-2")
        updates = [
            update
            async for update in graph.astream({"messages": [next_message]}, config=config)
        ]
        assert [name for update in updates for name in update] == list(node_names)
        assert first_node_history[-1] == [*result["messages"], next_message]
