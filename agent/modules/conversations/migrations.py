from __future__ import annotations

import json
import re

from sqlalchemy import MetaData, Table, create_engine, inspect, select, text

from agent.shared.thread_ids import canonical_thread_id
from agent.modules.conversations.models import ConversationThreadAlias

from agent.shared.infrastructure.db.engine import _normalize_url_to_sync

CONVERSATION_THREAD_COLUMNS: dict[str, str] = {
    "provider": "VARCHAR(255) NOT NULL DEFAULT ''",
    "model": "VARCHAR(255) NOT NULL DEFAULT ''",
}

_CANONICAL_ROOT_RE = re.compile(r"[a-f0-9]{32}")


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


def _is_canonical_root(root: str) -> bool:
    return bool(_CANONICAL_ROOT_RE.fullmatch(root or ""))


def _collect_thread_references(conn, tables: list[Table]) -> set[str]:
    """Collect thread IDs that may need migration.

    Uses a DB-side underscore prefilter (``LIKE '%\\_%'``) so steady-state
    boots with only opaque 32-hex IDs fetch zero rows instead of all
    ``DISTINCT thread_id`` values from large checkpoint tables. Canonical
    roots never contain underscores; legacy roots always do
    (``{platform}_{user}[_{channel}]``). False positives (canonical child
    IDs whose ``:sub:`` suffix contains an underscore) are filtered in
    Python via :func:`_is_canonical_root`.
    """
    references: set[str] = set()
    for table in tables:
        for column_name in ("thread_id", "root_thread_id"):
            if column_name not in table.c:
                continue
            column = table.c[column_name]
            try:
                rows = conn.execute(
                    select(column).where(column.like(r"%\_%", escape="\\")).distinct()
                ).scalars()
            except Exception:
                rows = conn.execute(select(column).distinct()).scalars()
            for value in rows:
                if value:
                    references.add(str(value))
    return references


def migrate_conversation_ids(database_url: str) -> dict[str, str]:
    """Rekey persisted conversations and checkpoint references atomically.

    Scope: migrates any ``{prefix}_...`` root (plus ``:sub:`` children) where
    ``prefix`` is a known platform (hardcoded set plus distinct platforms in
    ``conversation_threads``) or is inferred from persisted thread references
    across all scanned tables. The inference covers orphan checkpoint or
    workspace rows whose custom platform no longer has a
    ``conversation_threads`` row. Unrelated underscore IDs are also rekeyed
    safely because the legacy-to-canonical alias is persisted and
    :func:`resolve_thread_id` keeps old lookups working.

    Steady-state boots (no legacy references left) return after cheap
    prefiltered probes without scanning JSON payloads. This is safe because
    migration runs in a single transaction and new code only writes
    canonical IDs, so clean thread columns imply clean JSON payloads.
    """
    engine = create_engine(_normalize_url_to_sync(database_url), echo=False)
    try:
        with engine.begin() as conn:
            ConversationThreadAlias.__table__.create(conn, checkfirst=True)
            metadata = MetaData()
            table_names = set(inspect(conn).get_table_names())
            tables = [
                Table(name, metadata, autoload_with=conn)
                for name in (
                    "conversation_threads", "thread_workspaces", "background_tasks",
                    "llm_usage_events", "checkpoints", "writes", "checkpoint_writes",
                    "checkpoint_blobs",
                )
                if name in table_names
            ]
            alias_table = ConversationThreadAlias.__table__
            aliases = dict(conn.execute(select(alias_table.c.legacy_id, alias_table.c.canonical_id)).all())
            references = _collect_thread_references(conn, tables)
            prefixes = {"api", "cli", "telegram", "discord", "zalo", "task", "bg"}
            for table in tables:
                if table.name == "conversation_threads" and "platform" in table.c:
                    prefixes.update(
                        value for value in conn.execute(select(table.c.platform).distinct()).scalars()
                        if value and value != "unknown"
                    )
            for value in references:
                root = value.split(":sub:", 1)[0]
                if "_" in root and not _is_canonical_root(root):
                    prefixes.add(root.split("_", 1)[0])
            for value in sorted(references):
                root = value.split(":sub:", 1)[0]
                if root in aliases or _is_canonical_root(root):
                    continue
                if not any(root.startswith(f"{prefix}_") for prefix in prefixes):
                    continue
                aliases[root] = canonical_thread_id(root)
                conn.execute(alias_table.insert().values(legacy_id=root, canonical_id=aliases[root]))

            olds_to_update = [
                old for old in references
                if old.split(":sub:", 1)[0] in aliases
            ]
            if not olds_to_update:
                return aliases

            for old in olds_to_update:
                root, separator, suffix = old.partition(":sub:")
                if root not in aliases:
                    continue
                new = aliases[root] + separator + suffix
                for table in tables:
                    for column_name in ("thread_id", "root_thread_id"):
                        if column_name in table.c:
                            conn.execute(table.update().where(table.c[column_name] == old).values({column_name: new}))
                    if table.name == "conversation_threads":
                        conn.execute(table.update().where(table.c.thread_id == new, table.c.title == old).values(title=new))

            def rewrite_identity(value):
                if isinstance(value, dict):
                    result = {key: rewrite_identity(item) for key, item in value.items()}
                    for key in ("thread_id", "root_thread_id"):
                        if isinstance(result.get(key), str):
                            root, separator, suffix = result[key].partition(":sub:")
                            result[key] = aliases.get(root, root) + separator + suffix
                    return result
                if isinstance(value, list):
                    return [rewrite_identity(item) for item in value]
                return value

            for table in tables:
                for column_name in ("workspace_metadata_json", "execution_metadata_json", "scope_metadata_json"):
                    if column_name not in table.c:
                        continue
                    for row_id, raw in conn.execute(select(table.c.id, table.c[column_name])).all():
                        if not raw:
                            continue
                        if not any(f"{prefix}_" in raw for prefix in prefixes):
                            continue
                        try:
                            original = json.loads(raw)
                        except (ValueError, TypeError):
                            continue
                        rewritten = rewrite_identity(original)
                        if rewritten != original:
                            conn.execute(table.update().where(table.c.id == row_id).values({column_name: json.dumps(rewritten)}))
            return aliases
    finally:
        engine.dispose()


__all__ = ["migrate_conversation_tables", "migrate_conversation_ids"]
