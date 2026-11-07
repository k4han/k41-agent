"""Preserve existing project login choices while adding explicit inheritance."""

from sqlalchemy import create_engine, inspect, text

from agent.shared.infrastructure.db.engine import _normalize_url_to_sync


def migrate_browser_tables(database_url: str) -> None:
    engine = create_engine(_normalize_url_to_sync(database_url), echo=False)
    try:
        with engine.begin() as connection:
            inspector = inspect(connection)
            if "browser_project_profiles" not in inspector.get_table_names():
                return
            columns = {column["name"]: column for column in inspector.get_columns("browser_project_profiles")}
            if "mode" in columns:
                return
            if connection.dialect.name == "sqlite":
                # SQLite's legacy transaction mode does not begin a transaction for DDL.
                connection.exec_driver_sql("BEGIN")
                connection.execute(text("""
                    CREATE TABLE browser_project_profiles_upgrade (
                        id INTEGER NOT NULL PRIMARY KEY,
                        project_id INTEGER NOT NULL,
                        owner_id VARCHAR(255) NOT NULL,
                        mode VARCHAR(16) NOT NULL DEFAULT 'profile',
                        profile_id VARCHAR(64),
                        CONSTRAINT uq_browser_project_owner UNIQUE (project_id, owner_id)
                    )
                """))
                connection.execute(text("""
                    INSERT INTO browser_project_profiles_upgrade (id, project_id, owner_id, profile_id)
                    SELECT id, project_id, owner_id, profile_id FROM browser_project_profiles
                """))
                connection.execute(text("DROP TABLE browser_project_profiles"))
                connection.execute(text("ALTER TABLE browser_project_profiles_upgrade RENAME TO browser_project_profiles"))
                connection.execute(text("CREATE INDEX ix_browser_project_profiles_profile_id ON browser_project_profiles (profile_id)"))
            else:
                connection.execute(text("ALTER TABLE browser_project_profiles ADD COLUMN mode VARCHAR(16) NOT NULL DEFAULT 'profile'"))
                connection.execute(text("ALTER TABLE browser_project_profiles ALTER COLUMN profile_id DROP NOT NULL"))
    finally:
        engine.dispose()
