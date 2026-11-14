"""Read checkpoint identifiers and aggregates without loading checkpoint payloads."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langgraph.checkpoint.base import CheckpointTuple
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
except ImportError:
    AsyncPostgresSaver = None


def _backend(saver: object) -> str | None:
    if isinstance(saver, AsyncSqliteSaver):
        return "sqlite"
    if AsyncPostgresSaver is not None and isinstance(saver, AsyncPostgresSaver):
        return "postgres"
    return None


async def _fetch(saver, query: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    if _backend(saver) == "sqlite":
        await saver.setup()
        async with saver.lock, saver.conn.execute(query, params) as cursor:
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in await cursor.fetchall()]
    # Use the saver's cursor so its connection lock and pipeline are respected.
    async with saver._cursor() as cursor:
        await cursor.execute(query, params)
        return list(await cursor.fetchall())


def _metadata_expression(backend: str, key: str) -> str:
    if backend == "sqlite":
        return f"json_extract(CAST(metadata AS TEXT), '$.{key}')"
    return f"(metadata ->> '{key}')"


async def initialize_checkpoint_query_indexes(saver) -> None:
    """Build lookup indexes for existing and new checkpoint metadata."""
    backend = _backend(saver)
    if backend is None:
        return
    parent = _metadata_expression(backend, "k41_parent_thread_id")
    statements = []
    for suffix, key in (
        ("delegation", "k41_delegation_id"),
        ("tool_call", "k41_parent_tool_call_id"),
    ):
        value = _metadata_expression(backend, key)
        statements.append(
            f"CREATE INDEX IF NOT EXISTS k41_checkpoint_{suffix}_idx "
            f"ON checkpoints ({parent}, {value}, checkpoint_id DESC) "
            "WHERE checkpoint_ns = ''"
        )
    if backend == "postgres":
        statements.append(
            'CREATE INDEX IF NOT EXISTS k41_checkpoint_thread_prefix_idx '
            'ON checkpoints (thread_id COLLATE "C")'
        )
    if backend == "sqlite":
        await saver.setup()
        async with saver.lock:
            for statement in statements:
                async with saver.conn.execute(statement):
                    pass
            await saver.conn.commit()
    else:
        async with saver._cursor() as cursor:
            for statement in statements:
                await cursor.execute(statement)


async def list_checkpoint_child_thread_ids(saver, thread_id: str) -> set[str] | None:
    """Select descendants using an indexed prefix range, including nested children."""
    backend = _backend(saver)
    if backend is None:
        return None
    placeholder = "?" if backend == "sqlite" else "%s"
    column = "thread_id" if backend == "sqlite" else 'thread_id COLLATE "C"'
    rows = await _fetch(
        saver,
        f"SELECT DISTINCT thread_id FROM checkpoints WHERE {column} >= {placeholder} "
        f"AND {column} < {placeholder} ORDER BY thread_id",
        (f"{thread_id}:sub:", f"{thread_id}:sub;"),
    )
    return {row["thread_id"] for row in rows}


async def get_checkpoint_stats_batch(saver, thread_ids: Sequence[str]) -> dict[str, dict[str, Any]] | None:
    """Aggregate root checkpoints for a page of threads using the primary key."""
    backend = _backend(saver)
    if backend is None:
        return None
    normalized = list(dict.fromkeys(thread_ids))
    stats = {thread_id: {"latest_checkpoint_id": "", "checkpoint_count": 0} for thread_id in normalized}
    placeholder = "?" if backend == "sqlite" else "%s"
    # Bound parameters stay below SQLite limits even for unpaginated callers.
    for start in range(0, len(normalized), 500):
        batch = normalized[start:start + 500]
        placeholders = ", ".join([placeholder] * len(batch))
        rows = await _fetch(
            saver,
            "SELECT thread_id, COUNT(*) AS checkpoint_count, MAX(checkpoint_id) AS latest_checkpoint_id "
            f"FROM checkpoints WHERE checkpoint_ns = '' AND thread_id IN ({placeholders}) GROUP BY thread_id",
            batch,
        )
        for row in rows:
            stats[row["thread_id"]] = {
                "latest_checkpoint_id": row["latest_checkpoint_id"],
                "checkpoint_count": row["checkpoint_count"],
            }
    return stats


async def list_checkpoint_summaries(saver) -> list[dict[str, Any]] | None:
    """Read checkpoint-only legacy thread summaries without deserializing state."""
    if _backend(saver) is None:
        return None
    return await _fetch(
        saver,
        "SELECT thread_id, COUNT(*) AS checkpoint_count, MAX(checkpoint_id) AS latest_checkpoint_id "
        "FROM checkpoints WHERE checkpoint_ns = '' GROUP BY thread_id ORDER BY latest_checkpoint_id DESC",
    )


async def find_delegation_checkpoint(saver, criteria: dict[str, str]) -> CheckpointTuple | None:
    """Load only the newest root checkpoint matching indexed delegation metadata."""
    backend = _backend(saver)
    if backend is None:
        async for checkpoint in saver.alist(None, filter=criteria):
            if not checkpoint.config["configurable"].get("checkpoint_ns"):
                return checkpoint
        return None
    allowed_keys = {
        "k41_parent_thread_id", "k41_delegation_id", "k41_parent_tool_call_id", "k41_parent_message_id",
    }
    if not criteria.keys() <= allowed_keys:
        raise ValueError("Unsupported delegation metadata filter")
    placeholder = "?" if backend == "sqlite" else "%s"
    predicates = [f"{_metadata_expression(backend, key)} = {placeholder}" for key in criteria]
    rows = await _fetch(
        saver,
        "SELECT thread_id, checkpoint_id FROM checkpoints WHERE checkpoint_ns = '' AND "
        + " AND ".join(predicates) + " ORDER BY checkpoint_id DESC LIMIT 1",
        list(criteria.values()),
    )
    if not rows:
        return None
    return await saver.aget_tuple({"configurable": {**rows[0], "checkpoint_ns": ""}})
