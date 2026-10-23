from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from agent.shared.infrastructure.db.engine import _normalize_url_to_sync

WORKSPACE_COLUMNS: dict[str, str] = {
    "workspace_backend": "VARCHAR(50)",
    "workspace_locator": "TEXT",
    "workspace_label": "TEXT",
    "workspace_metadata_json": "TEXT",
}

WORKSPACE_IDENTITY_COLUMNS: dict[str, str] = {
    "project_id": "INTEGER",
    "project_workspace_id": "INTEGER",
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
# Default 'null' means unrestricted; '[]' means deny-all.
BACKGROUND_TASK_EXTRA_COLUMNS: dict[str, str] = {
    "allowed_tool_names_json": "TEXT NOT NULL DEFAULT 'null'",
    "allowed_skill_names_json": "TEXT NOT NULL DEFAULT 'null'",
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


def _backfill_legacy_workspaces(conn, table_name: str) -> None:
    from agent.modules.workspaces.refs import WorkspaceRef, derive_workspace_scope
    rows = conn.execute(text(f"SELECT * FROM {table_name} WHERE execution_locator IS NULL OR scope_key IS NULL")).mappings()
    for row in rows:
        locator = row["execution_locator"] or row["workspace_locator"] or row["working_dir"]
        if not locator:
            continue
        backend = row["execution_backend"] or row["workspace_backend"] or "local"
        try:
            metadata = json.loads(row["execution_metadata_json"] or row["workspace_metadata_json"] or "{}")
        except ValueError:
            metadata = {}
        if backend == "local":
            locator = str(Path(locator).expanduser().resolve())
        else:
            metadata.setdefault("root", "workspace" if backend == "daytona" else "/workspace")
        ref = WorkspaceRef(backend=backend, locator=locator, label=row["execution_label"] or row["workspace_label"] or locator,
                           metadata=metadata)
        scope = derive_workspace_scope(ref)
        conn.execute(text(f"UPDATE {table_name} SET execution_backend=:backend, execution_locator=:locator, "
                          "execution_label=:label, execution_metadata_json=:metadata, "
                          "scope_key=COALESCE(scope_key,:scope_key), scope_kind=COALESCE(scope_kind,:scope_kind), "
                          "scope_label=COALESCE(scope_label,:scope_label), scope_metadata_json=COALESCE(scope_metadata_json,:scope_metadata) "
                          "WHERE id=:id"),
                     {"id": row["id"], "backend": backend, "locator": locator, "label": ref.label,
                      "metadata": json.dumps(metadata), "scope_key": scope.key, "scope_kind": scope.kind,
                      "scope_label": scope.label, "scope_metadata": json.dumps(scope.metadata)})


def migrate_workspace_tables(database_url: str) -> None:
    """Add ownership columns and preserve references from older workspace schemas."""
    engine = create_engine(_normalize_url_to_sync(database_url), echo=False)
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            _ensure_columns(conn, inspector, "projects", {"migration_status": "VARCHAR(30) NOT NULL DEFAULT 'pending'"})
            _ensure_columns(conn, inspector, "project_workspaces", {"runtime_aliases_json": "TEXT NOT NULL DEFAULT '[]'"})
            for table_name in _TABLES_WITH_WORKSPACE_COLUMNS:
                _ensure_columns(conn, inspector, table_name, WORKSPACE_COLUMNS)
                _ensure_columns(conn, inspector, table_name, WORKSPACE_IDENTITY_COLUMNS)
                if _has_table(inspector, table_name):
                    for column in ("project_id", "project_workspace_id"):
                        conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_{column} ON {table_name} ({column})"))

            inspector = inspect(conn)
            if _has_table(inspector, "background_tasks"):
                _ensure_columns(
                    conn,
                    inspector,
                    "background_tasks",
                    BACKGROUND_TASK_EXTRA_COLUMNS,
                )

            inspector = inspect(conn)
            for table_name in _TABLES_WITH_WORKSPACE_COLUMNS:
                if _has_table(inspector, table_name):
                    _backfill_legacy_workspaces(conn, table_name)
    finally:
        engine.dispose()


__all__ = ["migrate_workspace_tables"]
