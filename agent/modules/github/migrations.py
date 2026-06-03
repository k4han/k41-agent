from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from agent.shared.infrastructure.db.engine import _normalize_url_to_sync


GITHUB_REPOSITORY_BINDING_COLUMNS: dict[str, str] = {
    "issue_label_enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
    "issue_comment_enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
    "pr_review_comment_enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
    "repository_instructions": "TEXT NOT NULL DEFAULT ''",
    "provider_name": "VARCHAR(255) NOT NULL DEFAULT ''",
    "model_name": "VARCHAR(255) NOT NULL DEFAULT ''",
    "context_trim_threshold": "INTEGER",
    "tool_policy_mode": "VARCHAR(50) NOT NULL DEFAULT 'inherit'",
    "allowed_tools_json": "TEXT NOT NULL DEFAULT '[]'",
    "branch_prefix": "VARCHAR(80) NOT NULL DEFAULT 'kaka'",
    "workspace_backend": "VARCHAR(50) NOT NULL DEFAULT 'local'",
}


def _has_table(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _column_names(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def migrate_github_tables(database_url: str) -> None:
    """Add per-repository automation and optimization columns."""
    engine = create_engine(_normalize_url_to_sync(database_url), echo=False)
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            table_name = "github_repository_bindings"
            if not _has_table(inspector, table_name):
                return

            existing = _column_names(inspector, table_name)
            for column_name, column_type in GITHUB_REPOSITORY_BINDING_COLUMNS.items():
                if column_name not in existing:
                    conn.execute(
                        text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                    )
    finally:
        engine.dispose()


__all__ = ["migrate_github_tables"]
