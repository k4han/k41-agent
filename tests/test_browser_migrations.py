import pytest
from sqlalchemy import create_engine, event, inspect, text

from agent.modules.browser import migrations
from agent.modules.browser.migrations import migrate_browser_tables


def test_legacy_browser_project_profiles_preserve_logins_and_allow_inheritance(tmp_path):
    url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE browser_project_profiles (
                    id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL,
                    owner_id VARCHAR(255) NOT NULL, profile_id VARCHAR(64) NOT NULL,
                    CONSTRAINT uq_browser_project_owner UNIQUE (project_id, owner_id)
                )
            """))
            connection.execute(text("CREATE INDEX ix_browser_project_profiles_profile_id ON browser_project_profiles (profile_id)"))
            connection.execute(text("INSERT INTO browser_project_profiles VALUES (7, 3, 'admin:1', 'saved-profile')"))
        migrate_browser_tables(url)
        migrate_browser_tables(url)
        with engine.begin() as connection:
            row = connection.execute(text("SELECT id, project_id, owner_id, mode, profile_id FROM browser_project_profiles")).one()
            assert tuple(row) == (7, 3, "admin:1", "profile", "saved-profile")
            connection.execute(text("INSERT INTO browser_project_profiles (project_id, owner_id, mode) VALUES (4, 'admin:1', 'inherit')"))
            assert connection.scalar(text("SELECT COUNT(*) FROM browser_project_profiles")) == 2
            inspector = inspect(connection)
            assert any(index["column_names"] == ["profile_id"] for index in inspector.get_indexes("browser_project_profiles"))
            assert any(constraint["column_names"] == ["project_id", "owner_id"]
                       for constraint in inspector.get_unique_constraints("browser_project_profiles"))
    finally:
        engine.dispose()


def test_failed_browser_migration_rolls_back_and_can_be_retried(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'rollback.db').as_posix()}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE browser_project_profiles (
                id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL,
                owner_id VARCHAR(255) NOT NULL, profile_id VARCHAR(64) NOT NULL,
                CONSTRAINT uq_browser_project_owner UNIQUE (project_id, owner_id)
            )
        """))
        connection.execute(text("INSERT INTO browser_project_profiles VALUES (1, 2, 'admin:1', 'keep-logins')"))

    def fail_before_rename(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("ALTER TABLE browser_project_profiles_upgrade"):
            raise RuntimeError("Interrupted migration")

    monkeypatch.setattr(migrations, "create_engine", lambda *args, **kwargs: engine)
    event.listen(engine, "before_cursor_execute", fail_before_rename)
    try:
        with pytest.raises(RuntimeError, match="Interrupted migration"):
            migrate_browser_tables(url)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT profile_id FROM browser_project_profiles")) == "keep-logins"
            assert "browser_project_profiles_upgrade" not in inspect(connection).get_table_names()
        event.remove(engine, "before_cursor_execute", fail_before_rename)
        migrate_browser_tables(url)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT mode, profile_id FROM browser_project_profiles")).one() == ("profile", "keep-logins")
    finally:
        engine.dispose()
