import pytest
from sqlalchemy import create_engine, inspect, text

from agent.modules.conversations.migrations import migrate_conversation_tables
from agent.modules.conversations.models import ConversationThread


@pytest.mark.parametrize("legacy_schema", [False, True])
def test_conversation_list_index_survives_repeated_migrations(tmp_path, legacy_schema):
    database_url = f"sqlite:///{(tmp_path / 'conversations.db').as_posix()}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            if legacy_schema:
                connection.execute(text("""
                    CREATE TABLE conversation_threads (
                        id INTEGER PRIMARY KEY, thread_id VARCHAR(512) NOT NULL,
                        kind VARCHAR(50) NOT NULL DEFAULT 'user',
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        deleted_at DATETIME
                    )
                """))
                connection.execute(text("INSERT INTO conversation_threads (thread_id) VALUES ('existing')"))
            else:
                ConversationThread.__table__.create(connection)
            indexes = inspect(connection).get_indexes("conversation_threads")
            assert any(
                index["column_names"] == ["deleted_at", "kind", "updated_at", "id"]
                for index in indexes
            ) is (not legacy_schema)

        migrate_conversation_tables(database_url)
        migrate_conversation_tables(database_url)

        with engine.connect() as connection:
            indexes = inspect(connection).get_indexes("conversation_threads")
            assert sum(
                index["column_names"] == ["deleted_at", "kind", "updated_at", "id"]
                for index in indexes
            ) == 1
            if legacy_schema:
                assert connection.execute(text(
                    "SELECT thread_id, kind, provider, model, graph_family FROM conversation_threads"
                )).one() == ("existing", "user", "", "", "")
    finally:
        engine.dispose()
