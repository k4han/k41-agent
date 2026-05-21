from __future__ import annotations

import json

from sqlalchemy import create_engine, inspect, text

from agent.shared.infrastructure.db.engine import _normalize_url_to_sync

WORKSPACE_COLUMNS: dict[str, str] = {
    "workspace_backend": "VARCHAR(50)",
    "workspace_locator": "TEXT",
    "workspace_label": "TEXT",
    "workspace_metadata_json": "TEXT",
}

_TABLES_WITH_WORKSPACE_COLUMNS = ("thread_workspaces", "background_tasks")


def _has_table(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_names(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _add_column(conn, table_name: str, column_name: str, column_type: str) -> None:
    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))


def _ensure_columns(conn, inspector, table_name: str, columns: dict[str, str]) -> None:
    if not _has_table(inspector, table_name):
        return
    existing = _column_names(inspector, table_name)
    for column_name, column_type in columns.items():
        if column_name not in existing:
            _add_column(conn, table_name, column_name, column_type)


def _backfill_workspace_columns(conn, table_name: str, metadata_default: str) -> None:
    conn.execute(
        text(
            f"UPDATE {table_name} "
            "SET workspace_backend = COALESCE(NULLIF(workspace_backend, ''), 'local'), "
            "workspace_locator = COALESCE(NULLIF(workspace_locator, ''), working_dir), "
            "workspace_label = COALESCE(NULLIF(workspace_label, ''), working_dir), "
            "workspace_metadata_json = COALESCE(NULLIF(workspace_metadata_json, ''), :metadata) "
            "WHERE working_dir IS NOT NULL "
            "AND (workspace_locator IS NULL OR workspace_locator = '' "
            "OR workspace_backend IS NULL OR workspace_backend = '' "
            "OR workspace_label IS NULL OR workspace_label = '' "
            "OR workspace_metadata_json IS NULL OR workspace_metadata_json = '')"
        ),
        {"metadata": metadata_default},
    )


def migrate_workspace_tables(database_url: str) -> None:
    """Add workspace ref columns and backfill legacy local path columns."""
    engine = create_engine(_normalize_url_to_sync(database_url), echo=False)
    metadata_default = json.dumps({})
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            for table_name in _TABLES_WITH_WORKSPACE_COLUMNS:
                _ensure_columns(conn, inspector, table_name, WORKSPACE_COLUMNS)

            inspector = inspect(conn)
            for table_name in _TABLES_WITH_WORKSPACE_COLUMNS:
                if not _has_table(inspector, table_name):
                    continue
                if "working_dir" not in _column_names(inspector, table_name):
                    continue
                _backfill_workspace_columns(conn, table_name, metadata_default)
    finally:
        engine.dispose()


__all__ = ["migrate_workspace_tables"]
