"""Add persistent session revocation to existing admin credentials."""

from sqlalchemy import inspect, text

from agent.shared.infrastructure.db.engine import create_sync_engine as create_engine


def migrate_admin_auth_tables(database_url: str) -> None:
    engine = create_engine(database_url, echo=False)
    try:
        with engine.begin() as connection:
            inspector = inspect(connection)
            if "admin_credentials" not in inspector.get_table_names():
                return
            columns = {
                column["name"] for column in inspector.get_columns("admin_credentials")
            }
            if "session_version" not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE admin_credentials ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0"
                    )
                )
    finally:
        engine.dispose()
