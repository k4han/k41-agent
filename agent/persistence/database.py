import os
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.engine import URL, make_url


DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///data/agent_state.db"

# SQLAlchemy async engine for application data (user management, etc.)
_async_engine = None
_async_session_maker = None


def get_database_url() -> str:
    """Return configured database URL, defaulting to local SQLite file."""
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def _get_parsed_url() -> URL:
    return make_url(get_database_url())


def get_database_type() -> str:
    """Return the database type based on the driver in the URL."""
    parsed = _get_parsed_url()
    drivername = parsed.drivername

    # Handle both sync and async driver prefixes
    if "+" in drivername:
        base_driver = drivername.split("+")[1]
    else:
        base_driver = drivername

    # Handle common cases - normalize to base driver name
    if base_driver in ("sqlite", "aiosqlite", "pysqlite"):
        return "sqlite"
    elif base_driver in ("postgresql", "asyncpg", "psycopg2", "psycopg"):
        return "postgres"
    else:
        raise ValueError(f"Unsupported database driver: {base_driver}. Use sqlite or postgresql.")


def get_sqlite_conn_string() -> str:
    """Return sqlite path/connection string expected by AsyncSqliteSaver."""
    parsed = _get_parsed_url()
    if parsed.drivername != "sqlite" and "sqlite" not in parsed.drivername:
        raise ValueError(
            "SQLite checkpointer requires a sqlite URL. "
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

    # Check if we're in test mode (allow any path)
    allow_any_path = os.getenv("PERSISTENCE_ALLOW_ANY_PATH", "false").lower() == "true"
    
    if allow_any_path:
        # In test mode, just return the resolved path
        return str(resolved_path)

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
            f"Please use a path within the current directory or './data/' subdirectory. "
            f"Set PERSISTENCE_ALLOW_ANY_PATH=true to bypass this check (not recommended for production)."
        )

    # Create parent directories if needed
    if resolved_path.parent and resolved_path.parent != cwd:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

    return str(resolved_path)


def get_postgres_conn_string() -> str:
    """Return postgres connection string for LangGraph checkpointer."""
    parsed = _get_parsed_url()

    if "postgresql" not in parsed.drivername:
        raise ValueError(
            "PostgreSQL checkpointer requires a postgresql URL. "
            f"Set DATABASE_URL to a postgresql URL, got: {get_database_url()}"
        )

    # Build connection string without the async driver prefix for psycopg
    # AsyncSqliteSaver expects: postgresql://user:pass@host/db
    # Not: postgresql+asyncpg://...
    username = parsed.username or ""
    password = parsed.password or ""
    host = parsed.host or "localhost"
    port = parsed.port or 5432
    database = parsed.database or "postgres"

    # Build the connection string
    creds = f"{username}:{password}@" if username else ""
    port_str = f":{port}" if port else ""
    conn_string = f"postgresql://{creds}{host}{port_str}/{database}"

    # Add query parameters if any (except driver)
    if parsed.query:
        # Filter out driver-related params
        params = "&".join(
            f"{k}={v}" for k, v in parsed.query.items()
            if k not in ("sslmode", "charset")
        )
        if params:
            conn_string += f"?{params}"

    return conn_string


async def initialize_async_engine() -> "create_async_engine":
    """
    Initialize and cache a SQLAlchemy async engine for application data.
    Used for user management and other application-specific data.
    """
    global _async_engine, _async_session_maker
    if _async_engine is not None:
        return _async_engine

    database_url = get_database_url()
    db_type = get_database_type()

    # Ensure we use async driver
    if db_type == "sqlite":
        # Convert sqlite to sqlite+aiosqlite if needed
        if not ("+" in database_url and "aiosqlite" in database_url):
            database_url = database_url.replace("sqlite://", "sqlite+aiosqlite://")
        _async_engine = create_async_engine(
            database_url,
            connect_args={"check_same_thread": False},
            future=True,
        )
    elif db_type == "postgres":
        # Convert postgresql to postgresql+asyncpg if needed
        if not ("+" in database_url and "asyncpg" in database_url):
            database_url = database_url.replace("postgresql://", "postgresql+asyncpg://")
        _async_engine = create_async_engine(
            database_url,
            pool_size=10,
            max_overflow=20,
            pool_timeout=30,
            pool_pre_ping=True,
            echo=False,
        )

    _async_session_maker = async_sessionmaker(
        _async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    return _async_engine


def get_async_engine():
    """Get the initialized SQLAlchemy async engine."""
    if _async_engine is None:
        raise RuntimeError(
            "Async engine not initialized. Call 'await initialize_async_engine()' first."
        )
    return _async_engine


def get_async_session_maker():
    """Get the async session maker."""
    if _async_session_maker is None:
        raise RuntimeError(
            "Async engine not initialized. Call 'await initialize_async_engine()' first."
        )
    return _async_session_maker


async def get_async_session() -> AsyncSession:
    """Create a new async session for database operations."""
    async_session_maker = get_async_session_maker()
    async with async_session_maker() as session:
        yield session


async def close_async_engine() -> None:
    """Dispose async engine used for application data."""
    global _async_engine, _async_session_maker

    if _async_engine is not None:
        await _async_engine.dispose()
        _async_engine = None
        _async_session_maker = None
