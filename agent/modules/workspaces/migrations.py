from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from agent.shared.infrastructure.db.engine import _normalize_url_to_sync

WORKSPACE_COLUMNS: dict[str, str] = {
    "workspace_backend": "VARCHAR(50)",
    "workspace_locator": "TEXT",
    "workspace_label": "TEXT",
    "workspace_metadata_json": "TEXT",
}

WORKSPACE_IDENTITY_COLUMNS: dict[str, str] = {
    "scope_key": "TEXT",
    "scope_kind": "VARCHAR(50)",
    "scope_label": "TEXT",
    "scope_metadata_json": "TEXT",
    "execution_backend": "VARCHAR(50)",
    "execution_locator": "TEXT",
    "execution_label": "TEXT",
    "execution_metadata_json": "TEXT",
}

# Columns that only the background_tasks table needs (tool/skill whitelists).
# Kept in this module so that the workspace migration stays the single source
# of truth for ``background_tasks`` schema evolution.
BACKGROUND_TASK_EXTRA_COLUMNS: dict[str, str] = {
    "allowed_tool_names_json": "TEXT NOT NULL DEFAULT '[]'",
    "allowed_skill_names_json": "TEXT NOT NULL DEFAULT '[]'",
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


def _delete_legacy_thread_workspaces(conn) -> None:
    conn.execute(
        text(
            "DELETE FROM thread_workspaces "
            "WHERE execution_locator IS NULL "
            "AND (working_dir IS NOT NULL "
            "OR workspace_locator IS NOT NULL "
            "OR workspace_backend IS NOT NULL)"
        )
    )


def migrate_workspace_tables(database_url: str) -> None:
    """Add workspace identity columns and reset legacy thread workspace mappings."""
    engine = create_engine(_normalize_url_to_sync(database_url), echo=False)
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            for table_name in _TABLES_WITH_WORKSPACE_COLUMNS:
                _ensure_columns(conn, inspector, table_name, WORKSPACE_COLUMNS)
                _ensure_columns(conn, inspector, table_name, WORKSPACE_IDENTITY_COLUMNS)

            inspector = inspect(conn)
            if _has_table(inspector, "background_tasks"):
                _ensure_columns(
                    conn,
                    inspector,
                    "background_tasks",
                    BACKGROUND_TASK_EXTRA_COLUMNS,
                )

            inspector = inspect(conn)
            if _has_table(inspector, "thread_workspaces"):
                _delete_legacy_thread_workspaces(conn)
    finally:
        engine.dispose()


__all__ = ["migrate_workspace_tables"]
