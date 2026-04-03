import pytest

import agent.shared.infrastructure.db.engine as db_engine


class _StubConfigService:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def get_str(self, key: str, default: str = "") -> str:
        if key == "database.url":
            return self._database_url
        return default


@pytest.fixture(autouse=True)
def _reset_engine_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db_engine, "_cached_database_url", None)


def _set_database_url(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    monkeypatch.setattr(db_engine, "get_config_service", lambda: _StubConfigService(database_url))


def test_database_url_defaults_to_internal_sqlite(monkeypatch: pytest.MonkeyPatch):
    _set_database_url(monkeypatch, "")

    assert db_engine.get_database_url() == db_engine.DEFAULT_DATABASE_URL
    assert db_engine.get_database_type() == "sqlite"


def test_database_url_accepts_postgres(monkeypatch: pytest.MonkeyPatch):
    postgres_url = "postgresql+asyncpg://user:pass@localhost:5432/app"
    _set_database_url(monkeypatch, postgres_url)

    assert db_engine.get_database_url() == postgres_url
    assert db_engine.get_database_type() == "postgres"


def test_database_url_rejects_custom_sqlite(monkeypatch: pytest.MonkeyPatch):
    _set_database_url(monkeypatch, "sqlite+aiosqlite:///./data/custom.db")

    with pytest.raises(ValueError, match="Custom SQLite URL is not allowed"):
        db_engine.get_database_url()


def test_database_url_rejects_unsupported_driver(monkeypatch: pytest.MonkeyPatch):
    _set_database_url(monkeypatch, "mysql://user:pass@localhost:3306/app")

    with pytest.raises(ValueError, match="Unsupported database driver"):
        db_engine.get_database_url()
