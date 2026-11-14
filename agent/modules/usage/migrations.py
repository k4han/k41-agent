"""Replace legacy usage indexes without rewriting event data."""

from sqlalchemy import inspect, text

from agent.modules.usage.models import LLMUsageEvent
from agent.shared.infrastructure.db.engine import create_sync_engine as create_engine


_LEGACY_INDEX_COLUMNS = (
    "thread_id",
    "platform",
    "user_id",
    "channel_id",
    "agent_name",
    "provider_name",
    "model_name",
    "call_kind",
    "internal",
    "has_usage_metadata",
    "run_id",
    "parent_run_id",
)


def migrate_usage_tables(database_url: str) -> None:
    engine = create_engine(database_url, echo=False)
    try:
        with engine.begin() as connection:
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN")
            inspector = inspect(connection)
            if not inspector.has_table(LLMUsageEvent.__tablename__):
                return
            existing_indexes = {
                index["name"]: index
                for index in inspector.get_indexes(LLMUsageEvent.__tablename__)
            }
            # Create replacements first so a failure keeps the old access paths.
            for index in sorted(LLMUsageEvent.__table__.indexes, key=lambda item: item.name):
                if index.name not in existing_indexes:
                    index.create(connection)
            # Only retire known, non-unique single-column indexes from the old model.
            for column_name in _LEGACY_INDEX_COLUMNS:
                index_name = f"ix_llm_usage_events_{column_name}"
                existing = existing_indexes.get(index_name)
                if (
                    existing
                    and not existing["unique"]
                    and existing["column_names"] == [column_name]
                ):
                    connection.execute(text(f"DROP INDEX {index_name}"))
    finally:
        engine.dispose()


__all__ = ["migrate_usage_tables"]
