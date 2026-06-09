from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from agent.shared.infrastructure.db.engine import _normalize_url_to_sync

CONVERSATION_THREAD_COLUMNS: dict[str, str] = {
    "provider": "VARCHAR(255) NOT NULL DEFAULT ''",
    "model": "VARCHAR(255) NOT NULL DEFAULT ''",
}


def _column_names(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def migrate_conversation_tables(database_url: str) -> None:
    engine = create_engine(_normalize_url_to_sync(database_url), echo=False)
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if "conversation_threads" not in inspector.get_table_names():
                return
            existing = _column_names(inspector, "conversation_threads")
            for column_name, column_type in CONVERSATION_THREAD_COLUMNS.items():
                if column_name not in existing:
                    conn.execute(
                        text(
                            "ALTER TABLE conversation_threads "
                            f"ADD COLUMN {column_name} {column_type}"
                        )
                    )
    finally:
        engine.dispose()


__all__ = ["migrate_conversation_tables"]
