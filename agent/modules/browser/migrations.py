"""Upgrade browser choices without moving or merging saved login directories."""

from sqlalchemy import create_engine, inspect, text

from agent.shared.infrastructure.db.engine import _normalize_url_to_sync


def migrate_browser_tables(database_url: str) -> None:
    engine = create_engine(_normalize_url_to_sync(database_url), echo=False)
    try:
        with engine.begin() as connection:
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN")
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            columns = set()
            if "browser_project_profiles" in tables:
                columns = {column["name"] for column in inspector.get_columns("browser_project_profiles")}
            if columns and "mode" not in columns and connection.dialect.name == "sqlite":
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
            elif columns and "mode" not in columns:
                connection.execute(text("ALTER TABLE browser_project_profiles ADD COLUMN mode VARCHAR(16) NOT NULL DEFAULT 'profile'"))
                connection.execute(text("ALTER TABLE browser_project_profiles ALTER COLUMN profile_id DROP NOT NULL"))
            if columns:
                connection.execute(text("UPDATE browser_project_profiles SET mode = 'inherit', profile_id = NULL WHERE mode = 'separate'"))
            if "browser_bindings" in tables:
                binding_columns = {column["name"] for column in inspector.get_columns("browser_bindings")}
                if "mode" not in binding_columns:
                    connection.execute(text("ALTER TABLE browser_bindings ADD COLUMN mode VARCHAR(16) NOT NULL DEFAULT 'inherit'"))
                    connection.execute(text("UPDATE browser_bindings SET mode = 'profile' WHERE profile_id IS NOT NULL"))
            if "browser_account_profiles" in tables:
                account_columns = {column["name"] for column in inspector.get_columns("browser_account_profiles")}
                if "temporary_profile_id" not in account_columns:
                    connection.execute(text("ALTER TABLE browser_account_profiles ADD COLUMN temporary_profile_id VARCHAR(64)"))
    finally:
        engine.dispose()
