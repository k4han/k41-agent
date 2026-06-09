from pathlib import Path

from sqlalchemy import MetaData
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agent.shared.config import get_config_service
from agent.shared.infrastructure.db.base import Base


def _get_default_db_path() -> str:
    """Get default database path in user's home directory."""
    home = Path.home()
    db_path = home / ".k41-agent" / "data" / "agent_state.db"
    # Use forward slashes for SQLite URL even on Windows
    return f"sqlite+aiosqlite:///{db_path.as_posix()}"


DEFAULT_DATABASE_URL = _get_default_db_path()

_async_engine: AsyncEngine | None = None
_async_session_maker: async_sessionmaker[AsyncSession] | None = None
_tables_created = False
_cached_database_url: str | None = None


def get_database_url() -> str:
    """Return effective database URL based on policy.

    Policy:
    - If database.url is empty, use internal SQLite path in ~/.k41-agent/data/
    - If database.url is set, only PostgreSQL URLs are allowed
    """
    global _cached_database_url
    if _cached_database_url is not None:
        return _cached_database_url

    config = get_config_service()
    config_url = config.get_str("database.url", "").strip()

    if not config_url:
        _cached_database_url = DEFAULT_DATABASE_URL
        return _cached_database_url

    try:
        parsed = make_url(config_url)
    except Exception as exc:
        raise ValueError(f"Invalid database.url: {config_url}") from exc

    base_driver = _extract_base_driver(parsed.drivername)
    if base_driver in ("postgresql", "asyncpg", "psycopg2", "psycopg"):
        _cached_database_url = config_url
        return _cached_database_url

    if base_driver in ("sqlite", "aiosqlite", "pysqlite"):
        raise ValueError(
            "Custom SQLite URL is not allowed in 'database.url'. "
            "Leave 'database.url' empty to use internal SQLite, "
            "or set a PostgreSQL URL."
        )

    raise ValueError(
        f"Unsupported database driver: {base_driver}. "
        "Use internal SQLite (empty 'database.url') or PostgreSQL URL."
    )


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
    raise ValueError(
        f"Unsupported database driver: {base_driver}. Use sqlite or postgresql."
    )


def get_sqlite_conn_string() -> str:
    """Return sqlite path/connection string expected by AsyncSqliteSaver."""
    parsed = _get_parsed_url()
    if parsed.drivername != "sqlite" and "sqlite" not in parsed.drivername:
        raise ValueError(
            "SQLite checkpointer requires a sqlite URL. "
            "Leave 'database.url' empty to use internal SQLite. "
            f"Current database URL: {get_database_url()}"
        )

    database = parsed.database
    if not database or database == ":memory:":
        return ":memory:"

    db_path = Path(database)
    try:
        resolved_path = db_path.resolve()
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid database path: {database}") from exc

    # Ensure parent directory exists
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    return str(resolved_path)


def get_postgres_conn_string() -> str:
    """Return postgres connection string for LangGraph checkpointer."""
    parsed = _get_parsed_url()

    if "postgresql" not in parsed.drivername:
        raise ValueError(
            "PostgreSQL checkpointer requires a postgresql URL. "
            "Set 'database.url' in ~/.k41-agent/config.yaml. "
            f"Current database URL: {get_database_url()}"
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
            database_url = database_url.replace(
                "postgresql://", "postgresql+asyncpg://"
            )
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
    global _async_engine, _async_session_maker, _tables_created, _cached_database_url

    if _async_engine is not None:
        await _async_engine.dispose()
        _async_engine = None
        _async_session_maker = None
        _tables_created = False
        _cached_database_url = None


def _normalize_url_to_sync(database_url: str) -> str:
    """Convert async driver URL to a sync driver URL for create_all()."""
    parsed = make_url(database_url)
    base_driver = _extract_base_driver(parsed.drivername)

    # Map async drivers to sync drivers
    sync_map = {
        "aiosqlite": "sqlite",
        "asyncpg": "postgresql+psycopg",
    }

    # If already a sync driver, return as-is
    if base_driver in ("sqlite", "postgresql", "psycopg", "psycopg2"):
        return database_url

    sync_driver = sync_map.get(base_driver)
    if sync_driver is None:
        raise ValueError(
            f"Unsupported async driver '{parsed.drivername}'. Cannot convert to sync driver."
        )
    normalized = parsed.set(drivername=sync_driver)
    return normalized.render_as_string(hide_password=False)


def create_tables(database_url: str, metadata: MetaData | None = None) -> None:
    """Create tables for the provided metadata using a sync engine."""
    target_metadata = metadata if metadata is not None else Base.metadata
    sync_url = _normalize_url_to_sync(database_url)

    # Ensure parent directory exists for SQLite databases
    parsed = make_url(database_url)
    if (
        "sqlite" in parsed.drivername
        and parsed.database
        and parsed.database != ":memory:"
    ):
        db_path = Path(parsed.database)
        db_path.parent.mkdir(parents=True, exist_ok=True)

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
