import pytest
from pytest import MonkeyPatch

from agent.persistence.database import (
    get_database_type,
    get_postgres_conn_string,
    get_sqlite_conn_string,
)


def test_get_database_type_sqlite_variants(monkeypatch: MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./data/agent_state.db")
    assert get_database_type() == "sqlite"

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./data/agent_state.db")
    assert get_database_type() == "sqlite"


def test_get_database_type_postgres_variants(monkeypatch: MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/app")
    assert get_database_type() == "postgres"

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/app")
    assert get_database_type() == "postgres"


def test_get_postgres_conn_string_preserves_query_params(monkeypatch: MonkeyPatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pass@db.example.com:5432/appdb?sslmode=require&application_name=kaka",
    )

    conn = get_postgres_conn_string()

    assert conn.startswith("postgresql://user:pass@db.example.com:5432/appdb")
    assert "sslmode=require" in conn
    assert "application_name=kaka" in conn


def test_get_postgres_conn_string_rejects_non_postgres(monkeypatch: MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./data/agent_state.db")

    with pytest.raises(ValueError):
        get_postgres_conn_string()


def test_get_sqlite_conn_string_memory(monkeypatch: MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    assert get_sqlite_conn_string() == ":memory:"


def test_get_sqlite_conn_string_rejects_non_sqlite(monkeypatch: MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/app")

    with pytest.raises(ValueError):
        get_sqlite_conn_string()
