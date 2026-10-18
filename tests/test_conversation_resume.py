from types import SimpleNamespace

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph

from agent.modules.conversations.history import get_thread_resume_error, record_thread_run_error


@pytest_asyncio.fixture
async def error_persistence(isolated_container):
    await isolated_container.initialize_persistence()
    try:
        yield isolated_container
    finally:
        await isolated_container.close_persistence()


@pytest.fixture
def resume_dependencies(monkeypatch):
    sessions = []
    monkeypatch.setattr(
        "agent.modules.agent_runtime.get_active_session_registry",
        lambda: SimpleNamespace(list_active=lambda: sessions),
    )
    monkeypatch.setattr(
        "agent.modules.agents.get_catalog_service",
        lambda: SimpleNamespace(get_agent=lambda name: SimpleNamespace(graph_type="react_agent")),
    )
    monkeypatch.setattr(
        "agent.modules.workflows.make_run_config",
        lambda **kwargs: {"configurable": {"thread_id": kwargs["thread_id"]}},
    )
    return sessions


@pytest.mark.asyncio
async def test_resume_error_survives_reopening_sqlite_checkpointer(
    tmp_path, monkeypatch, resume_dependencies, error_persistence,
):
    calls = {"prepare": 0, "respond": 0}

    async def prepare(state):
        calls["prepare"] += 1
        return {"messages": [AIMessage(content="Prepared", id="prepared")]}

    async def respond(state):
        calls["respond"] += 1
        if calls["respond"] == 1:
            raise TimeoutError("Provider request timed out after 30 seconds")
        return {"messages": [AIMessage(content="Recovered", id="recovered")]}

    builder = StateGraph(MessagesState)
    builder.add_node("prepare", prepare)
    builder.add_node("respond", respond)
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "respond")
    builder.add_edge("respond", END)
    database = str(tmp_path / "checkpoints.sqlite")
    config = {"configurable": {"thread_id": "failed-turn"}}
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        graph = builder.compile(checkpointer=saver)
        with pytest.raises(TimeoutError):
            await graph.ainvoke({"messages": [HumanMessage(content="Original request")]}, config)
        checkpoint_id = (await graph.aget_state(config)).config["configurable"]["checkpoint_id"]
        monkeypatch.setattr("agent.modules.workflows.get_workflow_graph", lambda name: graph)
        legacy_error = await get_thread_resume_error("failed-turn", checkpoint_id=checkpoint_id)
        assert legacy_error["content"] == "TimeoutError('Provider request timed out after 30 seconds')"
        assert legacy_error["message"] == "The AI provider took too long to respond. Please try again."
        monkeypatch.setattr("agent.modules.conversations.history.get_history_checkpointer", lambda: saver)
        original_error = {
            "type": "error", "code": "timeout",
            "content": "Provider took too long to respond.\nRequest ID: request-123",
            "message": "The request timed out. Please try again.",
        }
        await record_thread_run_error("failed-turn", original_error)

    await error_persistence.close_persistence()
    await error_persistence.initialize_persistence()

    # Rebuild the graph over a fresh connection, as after a server restart.
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        graph = builder.compile(checkpointer=saver)
        monkeypatch.setattr("agent.modules.workflows.get_workflow_graph", lambda name: graph)
        monkeypatch.setattr("agent.modules.conversations.history.get_history_checkpointer", lambda: saver)
        for _ in range(2):
            error = await get_thread_resume_error("failed-turn", checkpoint_id=checkpoint_id)
            assert error["checkpoint_id"] == checkpoint_id
            assert error["code"] == original_error["code"]
            assert error["content"] == original_error["content"]
            assert error["message"] == original_error["message"]
        retry_error = {"type": "error", "code": "upstream", "content": "Provider unavailable. Request ID: request-456"}
        await record_thread_run_error("failed-turn", retry_error)
        # A different thread sharing the checkpoint identifier must not leak its error.
        await error_persistence.conversation_repository.record_run_error(
            thread_id="other-turn", checkpoint_id=checkpoint_id, code="unknown", content="Other error",
        )
        assert await get_thread_resume_error("failed-turn", checkpoint_id=checkpoint_id) == {
            "checkpoint_id": checkpoint_id, "code": retry_error["code"], "content": retry_error["content"],
            "message": retry_error["content"],
        }
        assert await get_thread_resume_error("failed-turn", checkpoint_id="historical-branch") is None
        await graph.ainvoke(None, config)
        assert calls == {"prepare": 1, "respond": 2}

    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        graph = builder.compile(checkpointer=saver)
        assert await get_thread_resume_error("failed-turn") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("next_nodes", "has_messages", "task_interrupts", "state_interrupts", "error", "expected_code"),
    [
        ((), True, (), (), None, None),
        (("respond",), False, (), (), None, None),
        (("tools",), True, ("pending-answer",), (), None, None),
        (("tools",), True, (), ("pending-permission",), None, None),
        (("respond",), True, (), (), None, "execution_incomplete"),
        (("respond",), True, (), (), "TimeoutError()", "execution_failed"),
    ],
)
async def test_resume_error_respects_checkpoint_state(
    monkeypatch, resume_dependencies,
    next_nodes, has_messages, task_interrupts, state_interrupts, error, expected_code,
):
    async def get_state(config):
        return SimpleNamespace(
            config={"configurable": {"checkpoint_id": "latest"}},
            next=next_nodes,
            values={"messages": [HumanMessage(content="Request")] if has_messages else []},
            tasks=(SimpleNamespace(error=error, interrupts=task_interrupts),),
            interrupts=state_interrupts,
        )

    monkeypatch.setattr(
        "agent.modules.workflows.get_workflow_graph",
        lambda name: SimpleNamespace(aget_state=get_state),
    )
    result = await get_thread_resume_error("failed-turn", checkpoint_id="latest")
    assert (result["code"] if result else None) == expected_code
    if error:
        assert result["content"] == error


@pytest.mark.asyncio
async def test_resume_error_is_hidden_for_active_runs(monkeypatch, resume_dependencies):
    resume_dependencies.append({"thread_id": "failed-turn"})

    def unexpected_graph_lookup(name):
        pytest.fail("Active runs must not be treated as unfinished checkpoints")

    monkeypatch.setattr("agent.modules.workflows.get_workflow_graph", unexpected_graph_lookup)
    assert await get_thread_resume_error("failed-turn") is None


def test_saved_error_message_migration_preserves_existing_diagnostics(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    from agent.modules.conversations.migrations import migrate_conversation_tables

    database_url = f"sqlite:///{(tmp_path / 'legacy-errors.sqlite').as_posix()}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE conversation_run_errors (id INTEGER PRIMARY KEY, content TEXT NOT NULL)"))
            connection.execute(text("INSERT INTO conversation_run_errors (content) VALUES ('Original diagnostics')"))
        migrate_conversation_tables(database_url)
        migrate_conversation_tables(database_url)
        assert "message" in {column["name"] for column in inspect(engine).get_columns("conversation_run_errors")}
        with engine.connect() as connection:
            assert connection.execute(text("SELECT content, message FROM conversation_run_errors")).one() == (
                "Original diagnostics", "",
            )
    finally:
        engine.dispose()
