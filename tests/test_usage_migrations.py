from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Index, MetaData, create_engine, event, func, inspect, select, text

from agent.modules.usage import migrations
from agent.modules.usage.migrations import migrate_usage_tables
from agent.modules.usage.models import LLMUsageEvent
from agent.modules.usage.repository import UsageQuery, _where_clauses


_LEGACY_INDEX_COLUMNS = (
    "created_at", "thread_id", "root_thread_id", "platform", "user_id",
    "channel_id", "agent_name", "provider_name", "model_name", "call_kind",
    "internal", "has_usage_metadata", "run_id", "parent_run_id",
)


def _create_usage_table(connection, *, legacy_schema):
    table = LLMUsageEvent.__table__.to_metadata(MetaData())
    if legacy_schema:
        table.indexes.clear()
        for column_name in _LEGACY_INDEX_COLUMNS:
            Index(f"ix_llm_usage_events_{column_name}", table.c[column_name])
    table.create(connection)
    return table


@pytest.mark.parametrize("legacy_schema", [False, True])
def test_usage_indexes_migrate_repeatedly_without_changing_events(tmp_path, legacy_schema):
    database_url = f"sqlite:///{(tmp_path / 'usage.db').as_posix()}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            table = _create_usage_table(connection, legacy_schema=legacy_schema)
            Index("ix_custom_usage_runs", table.c.run_id, table.c.parent_run_id).create(connection)
            connection.execute(table.insert().values(
                thread_id="existing-thread", root_thread_id="existing-root",
                input_tokens=12, output_tokens=8, total_tokens=20,
                run_id="existing-run", parent_run_id="existing-parent",
                usage_metadata_json='{"source": "provider"}',
            ))
            original_events = connection.execute(select(table)).mappings().all()
            assert len(inspect(connection).get_indexes(table.name)) == (15 if legacy_schema else 11)

        migrate_usage_tables(database_url)
        migrate_usage_tables(database_url)

        with engine.connect() as connection:
            indexes = {index["name"]: index for index in inspect(connection).get_indexes(table.name)}
            assert len(indexes) == 11
            assert indexes["ix_custom_usage_runs"]["column_names"] == ["run_id", "parent_run_id"]
            assert sorted(
                index["column_names"][0] for index in indexes.values()
                if len(index["column_names"]) == 1
            ) == ["created_at", "root_thread_id"]
            assert connection.execute(select(table)).mappings().all() == original_events
    finally:
        engine.dispose()


def test_usage_migration_skips_absent_table(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'empty.db').as_posix()}"
    migrate_usage_tables(database_url)
    engine = create_engine(database_url)
    try:
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_usage_migration_rolls_back_index_changes_on_failure(tmp_path, monkeypatch):
    database_url = f"sqlite:///{(tmp_path / 'rollback.db').as_posix()}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            table = _create_usage_table(connection, legacy_schema=True)
        original_indexes = inspect(engine).get_indexes(table.name)

        def fail_during_drop(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith("DROP INDEX"):
                raise RuntimeError("index migration failed")

        event.listen(engine, "before_cursor_execute", fail_during_drop)
        monkeypatch.setattr(migrations, "create_engine", lambda *args, **kwargs: engine)
        with pytest.raises(RuntimeError, match="index migration failed"):
            migrate_usage_tables(database_url)
        assert inspect(engine).get_indexes(table.name) == original_indexes
    finally:
        engine.dispose()


@pytest.mark.parametrize("filter_name, value, index_name", [
    ("platform", "api", "ix_llm_usage_events_platform_created"),
    ("user_id", "user-1", "ix_llm_usage_events_user_created"),
    ("channel_id", "channel-1", "ix_llm_usage_events_channel_created"),
    ("agent_name", "worker", "ix_llm_usage_events_agent_created"),
    ("provider_name", "test-provider", "ix_llm_usage_events_provider_created"),
    ("model_name", "test-model", "ix_llm_usage_events_model_created"),
    ("call_kind", "agent", "ix_llm_usage_events_call_kind_created"),
    (None, None, "ix_llm_usage_events_created_at"),
])
def test_dashboard_range_queries_use_indexes_after_upgrade(tmp_path, filter_name, value, index_name):
    database_url = f"sqlite:///{(tmp_path / 'query-plan.db').as_posix()}"
    engine = create_engine(database_url)
    now = datetime(2026, 1, 15, tzinfo=timezone.utc)
    try:
        with engine.begin() as connection:
            table = _create_usage_table(connection, legacy_schema=True)
            connection.execute(table.insert(), [
                {
                    "created_at": now + timedelta(days=offset),
                    "platform": "api", "user_id": "user-1", "channel_id": "channel-1",
                    "agent_name": "worker", "provider_name": "test-provider",
                    "model_name": "test-model", "call_kind": "agent", "total_tokens": 20,
                }
                for offset in (-2, 0, 2)
            ])

        migrate_usage_tables(database_url)
        query = UsageQuery(
            start=now - timedelta(days=1), end=now + timedelta(days=1),
            **({filter_name: value} if filter_name else {}),
        )
        statement = select(func.sum(LLMUsageEvent.total_tokens)).where(*_where_clauses(query))
        sql = str(statement.compile(engine, compile_kwargs={"literal_binds": True}))
        with engine.connect() as connection:
            assert connection.execute(statement).scalar_one() == 20
            plan = " ".join(row[3] for row in connection.execute(text(f"EXPLAIN QUERY PLAN {sql}")))
        assert index_name in plan
        assert "created_at>? AND created_at<?" in plan
    finally:
        engine.dispose()


def test_latest_thread_event_uses_index_without_sorting(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'latest.db').as_posix()}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            table = _create_usage_table(connection, legacy_schema=True)
            connection.execute(table.insert(), [
                {"id": event_id, "thread_id": "thread-1", "created_at": datetime(2026, 1, 15)}
                for event_id in (1, 2)
            ])
        migrate_usage_tables(database_url)
        statement = (
            select(LLMUsageEvent.id)
            .where(LLMUsageEvent.thread_id == "thread-1", LLMUsageEvent.internal.is_(False))
            .order_by(LLMUsageEvent.created_at.desc(), LLMUsageEvent.id.desc())
            .limit(1)
        )
        sql = str(statement.compile(engine, compile_kwargs={"literal_binds": True}))
        with engine.connect() as connection:
            assert connection.execute(statement).scalar_one() == 2
            plan = " ".join(row[3] for row in connection.execute(text(f"EXPLAIN QUERY PLAN {sql}")))
        assert "ix_llm_usage_events_thread_created_id" in plan
        assert "TEMP B-TREE" not in plan
    finally:
        engine.dispose()
