import importlib
from types import SimpleNamespace

import pytest

app_module = importlib.import_module("agent.bootstrap.app")


def test_run_swallows_keyboard_interrupt(monkeypatch):
    called = {"asyncio_run": 0}

    def fake_asyncio_run(coro, *args, **kwargs):
        called["asyncio_run"] += 1
        coro.close()
        raise KeyboardInterrupt()

    monkeypatch.setattr(app_module.asyncio, "run", fake_asyncio_run)

    app_module.run()

    assert called["asyncio_run"] == 1


@pytest.mark.asyncio
async def test_web_server_uses_bounded_graceful_shutdown(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConfig:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeServer:
        should_exit = False

        def __init__(self, config):
            self.config = config

        async def serve(self):
            return None

    monkeypatch.setattr(
        app_module,
        "settings",
        SimpleNamespace(enable_web=True, host="127.0.0.1", port=4141),
    )
    monkeypatch.setattr(app_module, "app", object())
    monkeypatch.setattr(app_module.uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(app_module.uvicorn, "Server", FakeServer)

    await app_module.main()

    assert (
        captured["timeout_graceful_shutdown"]
        == app_module.SERVER_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS
    )


@pytest.mark.asyncio
async def test_favicon_endpoints():
    import httpx

    fastapi_app = app_module.create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url="http://testserver",
    ) as client:
        resp = await client.get("/favicon.ico")
        assert resp.status_code == 200
        assert "image/x-icon" in resp.headers.get("content-type", "")
        assert len(resp.content) > 0

        resp_yellow = await client.get("/favicon-yellow.ico")
        assert resp_yellow.status_code == 200
        assert "image/x-icon" in resp_yellow.headers.get("content-type", "")
        assert len(resp_yellow.content) > 0

