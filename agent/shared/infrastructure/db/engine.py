import os
from pathlib import Path

from sqlalchemy import MetaData
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from agent.shared.infrastructure.db.base import Base

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///data/agent_state.db"

_async_engine: AsyncEngine | None = None
_async_session_maker: async_sessionmaker[AsyncSession] | None = None
_tables_created = False


def get_database_url() -> str:
    """Return configured database URL, defaulting to local SQLite file."""
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def _get_parsed_url() -> URL:
    return make_url(get_database_url())


def _extract_base_driver(drivername: str) -> str:
    """Extract base driver name from compound drivername like 'sqlite+aiosqlite'."""
    if "+" in drivername:
        return drivername.split("+")[1]
    return drivername


def get_database_type() -> str:
    """Return the normalized database type based on the configured URL."""
    parsed = _get_parsed_url()
    base_driver = _extract_base_driver(parsed.drivername)

    if base_driver in ("sqlite", "aiosqlite", "pysqlite"):
        return "sqlite"
    if base_driver in ("postgresql", "asyncpg", "psycopg2", "psycopg"):
        return "postgres"
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

    db_path = Path(database)
    try:
        resolved_path = db_path.resolve()
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid database path: {database}") from exc

    allow_any_path = os.getenv("PERSISTENCE_ALLOW_ANY_PATH", "false").lower() == "true"
    if allow_any_path:
        return str(resolved_path)

    cwd = Path.cwd().resolve()
    is_allowed = False
    try:
        resolved_path.relative_to(cwd)
        is_allowed = True
    except ValueError:
        try:
            data_dir = cwd / "data"
            resolved_path.relative_to(data_dir)
            is_allowed = True
        except ValueError:
            pass

    if not is_allowed and resolved_path.is_absolute():
        safe_prefixes = ["/var/data", "/opt", "/home"]
        is_allowed = any(str(resolved_path).startswith(prefix) for prefix in safe_prefixes)

    if not is_allowed:
        raise ValueError(
            f"Database path '{database}' escapes allowed directories. "
            "Please use a path within the current directory or './data/' subdirectory. "
            "Set PERSISTENCE_ALLOW_ANY_PATH=true to bypass this check (not recommended for production)."
        )

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

    normalized = parsed.set(drivername="postgresql")
    return normalized.render_as_string(hide_password=False)


async def initialize_async_engine(metadata: MetaData | None = None) -> AsyncEngine:
    """Initialize and cache the async engine used by module repositories."""
    global _async_engine, _async_session_maker, _tables_created

    if _async_engine is not None:
        return _async_engine

    database_url = get_database_url()
    db_type = get_database_type()

    if db_type == "sqlite":
        if not ("+" in database_url and "aiosqlite" in database_url):
            database_url = database_url.replace("sqlite://", "sqlite+aiosqlite://")
        _async_engine = create_async_engine(
            database_url,
            connect_args={"check_same_thread": False},
            future=True,
        )
    elif db_type == "postgres":
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
    else:
        raise ValueError(f"Unsupported database type: {db_type}")

    _async_session_maker = async_sessionmaker(
        _async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    if not _tables_created:
        create_tables(database_url, metadata=metadata)
        _tables_created = True

    return _async_engine


def get_async_engine() -> AsyncEngine:
    """Return the initialized async engine."""
    if _async_engine is None:
        raise RuntimeError(
            "Async engine not initialized. Call 'await initialize_async_engine()' first."
        )
    return _async_engine


def _get_async_session_maker() -> async_sessionmaker[AsyncSession]:
    if _async_session_maker is None:
        raise RuntimeError(
            "Async engine not initialized. Call 'await initialize_async_engine()' first."
        )
    return _async_session_maker


async def close_async_engine() -> None:
    """Dispose the async engine and clear cached session state."""
    global _async_engine, _async_session_maker, _tables_created

    if _async_engine is not None:
        await _async_engine.dispose()
        _async_engine = None
        _async_session_maker = None
        _tables_created = False


def _normalize_url_to_sync(database_url: str) -> str:
    """Convert async driver URL to a sync driver URL for create_all()."""
    parsed = make_url(database_url)
    base_driver = _extract_base_driver(parsed.drivername)
    sync_map = {
        "aiosqlite": "sqlite",
        "asyncpg": "postgresql+psycopg",
    }
    sync_driver = sync_map.get(base_driver)
    if sync_driver is None:
        raise ValueError(f"Unsupported async driver '{parsed.drivername}'. Cannot convert to sync driver.")
    normalized = parsed.set(drivername=sync_driver)
    return normalized.render_as_string(hide_password=False)


def create_tables(database_url: str, metadata: MetaData | None = None) -> None:
    """Create tables for the provided metadata using a sync engine."""
    target_metadata = metadata if metadata is not None else Base.metadata
    sync_url = _normalize_url_to_sync(database_url)

    engine = create_sync_engine(sync_url, echo=False)
    try:
        target_metadata.create_all(engine)
    finally:
        engine.dispose()


__all__ = [
    "DEFAULT_DATABASE_URL",
    "close_async_engine",
    "create_tables",
    "get_async_engine",
    "get_database_type",
    "get_database_url",
    "get_postgres_conn_string",
    "get_sqlite_conn_string",
    "initialize_async_engine",
]
