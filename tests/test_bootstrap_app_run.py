import importlib

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
