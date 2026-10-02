import pytest

import agent.shared.infrastructure.db.engine as db_engine
from agent.bootstrap.container import AppContainer


class _StubConfigService:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def get_str(self, key: str, default: str = "") -> str:
        if key == "database.url":
            return self._database_url
        return default


@pytest.fixture
def stub_config(activate_container):
    def _activate(database_url: str) -> AppContainer:
        return activate_container(
            AppContainer(config_service=_StubConfigService(database_url))
        )

    yield _activate


def test_database_url_defaults_to_internal_sqlite(stub_config):
    stub_config("")

    assert db_engine.get_database_url() == db_engine.DEFAULT_DATABASE_URL
    assert db_engine.get_database_type() == "sqlite"


def test_database_url_accepts_postgres(stub_config):
    postgres_url = "postgresql+asyncpg://user:pass@localhost:5432/app"
    stub_config(postgres_url)

    assert db_engine.get_database_url() == postgres_url
    assert db_engine.get_database_type() == "postgres"


def test_database_url_rejects_custom_sqlite(stub_config):
    stub_config("sqlite+aiosqlite:///./data/custom.db")

    with pytest.raises(ValueError, match="Custom SQLite URL is not allowed"):
        db_engine.get_database_url()


def test_database_url_rejects_unsupported_driver(stub_config):
    stub_config("mysql://user:pass@localhost:3306/app")

    with pytest.raises(ValueError, match="Unsupported database driver"):
        db_engine.get_database_url()
