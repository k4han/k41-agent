"""Verify persistence readiness failures and atomic configuration updates."""

from types import SimpleNamespace

import httpx
import pytest


@pytest.mark.asyncio
async def test_ready_reflects_database_failure_without_changing_liveness(tmp_path):
    from sqlalchemy.ext.asyncio import create_async_engine
    from agent.bootstrap.app import create_app
    from agent.bootstrap.container import create_test_container

    container = create_test_container(tmp_path=tmp_path)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'ready.db').as_posix()}"
    )
    container._async_engine = engine
    container._checkpointer = object()
    app = create_app(container=container)
    app.state.runtime._started = True
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        assert (await client.get("/ready")).status_code == 503
        container._persistence_ready = True
        assert (await client.get("/ready")).status_code == 200
        await engine.dispose()
        # A stopped or unavailable database must never report readiness.
        container._async_engine = SimpleNamespace(
            connect=lambda: (_ for _ in ()).throw(ConnectionError("offline"))
        )
        assert (await client.get("/ready")).status_code == 503
        assert (await client.get("/health")).status_code == 200
        container._async_engine = engine
        container._checkpointer = None
        assert (await client.get("/ready")).status_code == 503


def test_failed_config_publication_preserves_previous_secret(tmp_path, monkeypatch):
    from agent.shared.config import yaml_source

    path = tmp_path / "config.yaml"
    source = yaml_source.YamlConfigSource(path=path)
    source.update_setting("security.jwt_secret", "original")

    def fail_replace(*args):
        raise OSError("publication failed")

    monkeypatch.setattr(yaml_source.os, "replace", fail_replace)
    with pytest.raises(OSError, match="publication failed"):
        source.update_setting("security.jwt_secret", "replacement")
    assert "original" in path.read_text()
    assert "replacement" not in path.read_text()
    assert list(tmp_path.glob("*.tmp")) == []
