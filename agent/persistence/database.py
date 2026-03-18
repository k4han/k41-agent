import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.engine import Engine

DEFAULT_DATABASE_URL = "sqlite:///data/agent_state.db"

_engine: Engine | None = None


def get_database_url() -> str:
    """Return configured database URL, defaulting to local SQLite file."""
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def _get_parsed_url() -> URL:
    return make_url(get_database_url())


def get_sqlite_conn_string() -> str:
    """Return sqlite path/connection string expected by AsyncSqliteSaver."""
    parsed = _get_parsed_url()
    if parsed.drivername != "sqlite":
        raise ValueError(
            "Only SQLite is supported for now. "
            f"Set DATABASE_URL to a sqlite URL, got: {get_database_url()}"
        )

    database = parsed.database
    if not database or database == ":memory:":
        return database

    # Only allow relative paths or absolute paths within safe locations
    db_path = Path(database)

    # Resolve to absolute path
    try:
        resolved_path = db_path.resolve()
    except (OSError, ValueError) as e:
        raise ValueError(f"Invalid database path: {database}") from e

    # Get the current working directory as base
    cwd = Path.cwd().resolve()

    # Check if path is within cwd or cwd/data directory
    is_allowed = False
    try:
        # Check if path is relative to cwd (will raise ValueError if not)
        resolved_path.relative_to(cwd)
        is_allowed = True
    except ValueError:
        # Not relative to cwd, check if it's in cwd/data
        try:
            data_dir = cwd / "data"
            resolved_path.relative_to(data_dir)
            is_allowed = True
        except ValueError:
            pass

    if not is_allowed:
        # For absolute paths, also check common safe locations
        if resolved_path.is_absolute():
            safe_prefixes = ["/var/data", "/opt", "/home"]
            is_allowed = any(
                str(resolved_path).startswith(prefix) for prefix in safe_prefixes
            )

    if not is_allowed:
        raise ValueError(
            f"Database path '{database}' escapes allowed directories. "
            f"Please use a path within the current directory or './data/' subdirectory."
        )

    # Create parent directories if needed
    if resolved_path.parent and resolved_path.parent != cwd:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

    return str(resolved_path)


def initialize_engine() -> Engine:
    """Initialize and cache a SQLAlchemy engine for SQLite-based persistence."""
    global _engine
    if _engine is not None:
        return _engine

    database_url = get_database_url()
    parsed = _get_parsed_url()
    if parsed.drivername != "sqlite":
        raise ValueError(
            "Only SQLite is supported for now. "
            f"Set DATABASE_URL to a sqlite URL, got: {database_url}"
        )

    _engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
        future=True,
    )
    return _engine


def get_engine() -> Engine:
    """Get the initialized SQLAlchemy engine."""
    if _engine is None:
        return initialize_engine()
    return _engine


def close_engine() -> None:
    """Dispose SQLAlchemy engine used for DB URL validation and lifecycle."""
    global _engine

    if _engine is not None:
        _engine.dispose()
        _engine = None
