from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from langgraph.checkpoint.base import empty_checkpoint

from agent.modules.conversations import history
from agent.modules.workflows import delete_workflow_thread_tree
from agent.modules.workflows.checkpoint import queries
from agent.modules.workflows.checkpoint.store import _create_serializer, _create_sqlite_saver


async def _save(saver, thread_id, checkpoint_id, *, namespace="", metadata=None):
    checkpoint = {**empty_checkpoint(), "id": checkpoint_id}
    return await saver.aput(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": namespace}},
        checkpoint, metadata or {}, {},
    )


@pytest_asyncio.fixture
async def sqlite_saver(tmp_path):
    async with _create_sqlite_saver(str(tmp_path / "checkpoints.sqlite"), _create_serializer()) as saver:
        await saver.setup()
        yield saver


@pytest.mark.asyncio
async def test_sqlite_saver_configures_concurrency_pragmas(sqlite_saver):
    for pragma, expected in (("journal_mode", "wal"), ("synchronous", 1), ("busy_timeout", 30_000)):
        async with sqlite_saver.conn.execute(f"PRAGMA {pragma}") as cursor:
            assert (await cursor.fetchone())[0] == expected


@pytest.mark.asyncio
async def test_batch_stats_do_not_decode_payloads_or_issue_per_thread_queries(sqlite_saver, monkeypatch):
    await _save(sqlite_saver, "first", "001")
    await _save(sqlite_saver, "first", "002")
    await _save(sqlite_saver, "first", "999", namespace="nested")
    await _save(sqlite_saver, "second", "003")
    await _save(sqlite_saver, "unrelated", "999")
    await sqlite_saver.conn.execute("UPDATE checkpoints SET checkpoint = X'00'")
    await sqlite_saver.conn.commit()
    monkeypatch.setattr(history, "get_history_checkpointer", lambda: sqlite_saver)
    monkeypatch.setattr(history, "get_checkpoint_stats", AsyncMock(side_effect=AssertionError("N+1 query")))
    statements = []
    await sqlite_saver.conn.set_trace_callback(statements.append)

    rows = await history._attach_checkpoint_stats([
        {"thread_id": "first", "title": "First"}, {"thread_id": "second"}, {"thread_id": "missing"},
    ])

    assert rows == [
        {"thread_id": "first", "title": "First", "latest_checkpoint_id": "002", "checkpoint_count": 2},
        {"thread_id": "second", "latest_checkpoint_id": "003", "checkpoint_count": 1},
        {"thread_id": "missing", "latest_checkpoint_id": "", "checkpoint_count": 0},
    ]
    selects = [query for query in statements if query.startswith("SELECT")]
    assert len(selects) == 1
    async with sqlite_saver.conn.execute("EXPLAIN QUERY PLAN " + selects[0]) as cursor:
        assert any("SEARCH checkpoints USING COVERING INDEX" in row[-1] for row in await cursor.fetchall())


@pytest.mark.asyncio
async def test_single_thread_stats_use_aggregate_and_reflect_upserts_and_deletion(sqlite_saver, monkeypatch):
    monkeypatch.setattr(history, "get_history_checkpointer", lambda: sqlite_saver)
    await _save(sqlite_saver, "thread", "001")
    await _save(sqlite_saver, "thread", "001")
    await _save(sqlite_saver, "thread", "002")
    assert await history.get_checkpoint_stats("thread") == {
        "latest_checkpoint_id": "002", "checkpoint_count": 2,
    }
    await sqlite_saver.adelete_thread("thread")
    assert await history.get_checkpoint_stats("thread") == {
        "latest_checkpoint_id": "", "checkpoint_count": 0,
    }


@pytest.mark.asyncio
async def test_batch_stats_chunk_large_unpaginated_requests(sqlite_saver):
    await _save(sqlite_saver, "thread-500", "001")
    thread_ids = [f"thread-{index}" for index in range(501)]
    stats = await queries.get_checkpoint_stats_batch(sqlite_saver, [*thread_ids, "thread-500"])
    assert len(stats) == 501
    assert stats["thread-500"] == {"latest_checkpoint_id": "001", "checkpoint_count": 1}
    assert await queries.get_checkpoint_stats_batch(sqlite_saver, []) == {}


@pytest.mark.asyncio
async def test_legacy_thread_summaries_do_not_deserialize_and_keep_pagination(sqlite_saver, monkeypatch):
    await _save(sqlite_saver, "", "999")
    await _save(sqlite_saver, "api_dashboard_first", "001")
    await _save(sqlite_saver, "api_dashboard_first", "002")
    await _save(sqlite_saver, "api_dashboard_second", "003")
    await _save(sqlite_saver, "api_dashboard_first", "999", namespace="nested")
    await _save(sqlite_saver, "api_dashboard_first:sub:child:1", "999")
    await _save(sqlite_saver, "task_background", "999")
    await sqlite_saver.conn.execute("UPDATE checkpoints SET checkpoint = X'00'")
    await sqlite_saver.conn.commit()
    monkeypatch.setattr(history, "get_history_checkpointer", lambda: sqlite_saver)
    rows = await history.list_legacy_threads_from_checkpoints()
    assert [row["thread_id"] for row in rows] == ["api_dashboard_second", "api_dashboard_first"]
    assert rows[1]["checkpoint_count"] == 2
    assert rows[1]["latest_checkpoint_id"] == "002"
    assert await history.list_legacy_threads_from_checkpoints(offset=1, limit=1) == rows[1:]


@pytest.mark.asyncio
async def test_checkpoint_initialization_closes_connection_when_index_creation_fails(monkeypatch, tmp_path):
    from agent.modules.workflows.checkpoint import store

    connections = []
    create_saver = store._create_sqlite_saver

    @asynccontextmanager
    async def capture_connection(*args, **kwargs):
        async with create_saver(*args, **kwargs) as saver:
            connections.append(saver.conn)
            yield saver

    monkeypatch.setattr(store, "_create_sqlite_saver", capture_connection)
    monkeypatch.setattr(store, "initialize_checkpoint_query_indexes", AsyncMock(side_effect=RuntimeError("index failed")))
    with pytest.raises(RuntimeError, match="index failed"):
        await store._build_checkpointer(f"sqlite:///{(tmp_path / 'failed.sqlite').as_posix()}")
    with pytest.raises(ValueError, match="no active connection"):
        await connections[0].execute("SELECT 1")


@pytest.mark.asyncio
async def test_thread_tree_deletion_uses_indexed_prefix_without_deserialization(sqlite_saver, monkeypatch):
    parent = "owner_%'"
    descendants = {f"{parent}:sub:child:1", f"{parent}:sub:child:1:sub:nested:2"}
    unrelated = {f"{parent}-other:sub:child:3", f"{parent}:submarine:child:4", "ownerXabc:sub:child:5"}
    for thread_id in {parent, *descendants, *unrelated}:
        await _save(sqlite_saver, thread_id, "001")
        await _save(sqlite_saver, thread_id, "002", namespace="nested")
    await sqlite_saver.conn.execute("UPDATE checkpoints SET checkpoint = X'00'")
    await sqlite_saver.conn.commit()
    monkeypatch.setattr(sqlite_saver, "alist", AsyncMock(side_effect=AssertionError("Full checkpoint scan")))
    monkeypatch.setattr("agent.modules.workflows.checkpoint.store.get_checkpointer", lambda: sqlite_saver)
    monkeypatch.setattr("agent.modules.acp.peek_acp_service", lambda: None)
    monkeypatch.setattr("agent.modules.browser.delete_thread_browsers", AsyncMock())
    monkeypatch.setattr("agent.modules.tools.clear_conversation_storage", AsyncMock())
    monkeypatch.setattr("agent.modules.workflows._release_thread_machines", AsyncMock())
    statements = []
    await sqlite_saver.conn.set_trace_callback(statements.append)

    await delete_workflow_thread_tree(parent)

    async with sqlite_saver.conn.execute("SELECT DISTINCT thread_id FROM checkpoints") as cursor:
        assert {row[0] for row in await cursor.fetchall()} == unrelated
    query = next(query for query in statements if query.startswith("SELECT DISTINCT thread_id FROM checkpoints WHERE"))
    async with sqlite_saver.conn.execute("EXPLAIN QUERY PLAN " + query) as cursor:
        assert any("SEARCH checkpoints USING COVERING INDEX" in row[-1] for row in await cursor.fetchall())


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", ["delegation", "tool_call", "message"])
async def test_delegation_lookup_indexes_existing_rows_and_loads_only_latest_root(sqlite_saver, monkeypatch, selector):
    metadata = {
        "k41_parent_thread_id": "parent_%'", "k41_delegation_id": "delegation", "k41_parent_tool_call_id": "call",
        "k41_parent_message_id": "message", "k41_agent_name": "child",
    }
    await _save(sqlite_saver, "child", "001", metadata=metadata)
    await _save(sqlite_saver, "child", "002", metadata=metadata)
    await _save(sqlite_saver, "child", "999", namespace="nested", metadata=metadata)
    await _save(sqlite_saver, "unrelated", "999", metadata={**metadata, "k41_parent_thread_id": "other"})
    # Index creation must work on legacy data and be safe to repeat at startup.
    await queries.initialize_checkpoint_query_indexes(sqlite_saver)
    await queries.initialize_checkpoint_query_indexes(sqlite_saver)
    monkeypatch.setattr(sqlite_saver, "alist", AsyncMock(side_effect=AssertionError("Full checkpoint scan")))
    load_checkpoint = AsyncMock(wraps=sqlite_saver.aget_tuple)
    monkeypatch.setattr(sqlite_saver, "aget_tuple", load_checkpoint)
    criteria = {"k41_parent_thread_id": metadata["k41_parent_thread_id"]}
    if selector == "delegation":
        criteria["k41_delegation_id"] = "delegation"
    else:
        criteria["k41_parent_tool_call_id"] = "call"
        if selector == "message":
            criteria["k41_parent_message_id"] = "message"
    statements = []
    await sqlite_saver.conn.set_trace_callback(statements.append)

    checkpoint = await queries.find_delegation_checkpoint(sqlite_saver, criteria)

    assert checkpoint.config["configurable"] == {
        "thread_id": "child", "checkpoint_ns": "", "checkpoint_id": "002",
    }
    load_checkpoint.assert_awaited_once()
    query = next(query for query in statements if query.startswith("SELECT thread_id, checkpoint_id FROM checkpoints"))
    async with sqlite_saver.conn.execute("EXPLAIN QUERY PLAN " + query) as cursor:
        plan = " ".join(row[-1] for row in await cursor.fetchall())
        assert "SEARCH checkpoints USING INDEX k41_checkpoint_" in plan
        assert "TEMP B-TREE" not in plan
    criteria["k41_parent_thread_id"] = "missing"
    assert await queries.find_delegation_checkpoint(sqlite_saver, criteria) is None
    load_checkpoint.assert_awaited_once()
    await sqlite_saver.adelete_thread("child")
    criteria["k41_parent_thread_id"] = metadata["k41_parent_thread_id"]
    assert await queries.find_delegation_checkpoint(sqlite_saver, criteria) is None


@pytest.mark.asyncio
async def test_batch_stats_failure_keeps_threads_without_retrying_checkpoint_scan(monkeypatch):
    monkeypatch.setattr(history, "get_history_checkpointer", lambda: object())
    monkeypatch.setattr("agent.modules.workflows.get_checkpoint_stats_batch", AsyncMock(side_effect=RuntimeError("db failed")))
    fallback = AsyncMock(side_effect=AssertionError("Unexpected checkpoint scan"))
    monkeypatch.setattr(history, "get_checkpoint_stats", fallback)
    assert await history._attach_checkpoint_stats([{"thread_id": "thread"}]) == [
        {"thread_id": "thread", "latest_checkpoint_id": "", "checkpoint_count": 0},
    ]
    fallback.assert_not_awaited()


@pytest.mark.asyncio
async def test_postgres_queries_share_saver_cursor_and_use_bound_parameters(monkeypatch):
    postgres_type = queries.AsyncPostgresSaver
    if postgres_type is None:
        pytest.skip("PostgreSQL checkpointer is not installed")
    saver = object.__new__(postgres_type)
    cursor = AsyncMock()
    cursor.fetchall.return_value = []

    @asynccontextmanager
    async def cursor_context():
        yield cursor

    monkeypatch.setattr(saver, "_cursor", cursor_context)
    await queries.initialize_checkpoint_query_indexes(saver)
    assert cursor.execute.await_count == 3
    cursor.execute.reset_mock()
    assert await queries.list_checkpoint_child_thread_ids(saver, "parent_%'") == set()
    query, params = cursor.execute.call_args.args
    assert 'thread_id COLLATE "C" >= %s' in query
    assert params == ("parent_%':sub:", "parent_%':sub;")
    cursor.fetchall.return_value = [{"thread_id": "parent", "latest_checkpoint_id": "002", "checkpoint_count": 2}]
    assert (await queries.get_checkpoint_stats_batch(saver, ["parent"]))["parent"]["checkpoint_count"] == 2
    cursor.fetchall.return_value = [{"thread_id": "child", "checkpoint_id": "002"}]
    load_checkpoint = AsyncMock(return_value="checkpoint")
    monkeypatch.setattr(saver, "aget_tuple", load_checkpoint)
    assert await queries.find_delegation_checkpoint(saver, {"k41_parent_thread_id": "parent"}) == "checkpoint"
    query, params = cursor.execute.call_args.args
    assert "metadata ->> 'k41_parent_thread_id'" in query
    assert "LIMIT 1" in query
    assert params == ["parent"]
    load_checkpoint.assert_awaited_once_with({"configurable": {
        "thread_id": "child", "checkpoint_id": "002", "checkpoint_ns": "",
    }})
