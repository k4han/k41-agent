from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from agent.shared.infrastructure.db.engine import _normalize_url_to_sync


BACKGROUND_TASK_COLUMNS: dict[str, str] = {
    "task_timeout": "FLOAT",
    "max_retries": "INTEGER NOT NULL DEFAULT 0",
    "retry_count": "INTEGER NOT NULL DEFAULT 0",
}


def _has_table(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_names(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def migrate_agent_runtime_tables(database_url: str) -> None:
    """Add timeout and retry columns to background_tasks table."""
    engine = create_engine(_normalize_url_to_sync(database_url), echo=False)
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            table_name = "background_tasks"
            if not _has_table(inspector, table_name):
                return

            existing = _column_names(inspector, table_name)
            for column_name, column_type in BACKGROUND_TASK_COLUMNS.items():
                if column_name not in existing:
                    conn.execute(
                        text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                    )
    finally:
        engine.dispose()


__all__ = ["migrate_agent_runtime_tables"]
