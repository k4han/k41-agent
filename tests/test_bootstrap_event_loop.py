import asyncio
import importlib
import sys
from types import SimpleNamespace

import pytest

from agent.bootstrap import event_loop


@pytest.mark.parametrize("platform,database_type,selector", [
    ("win32", "sqlite", False),
    ("win32", "postgres", True),
    ("linux", "sqlite", False),
    ("linux", "postgres", False),
])
def test_loop_factory_preserves_postgres_compatibility(monkeypatch, caplog, platform, database_type, selector):
    monkeypatch.setattr(event_loop, "sys", SimpleNamespace(platform=platform))
    factory = event_loop.get_event_loop_factory(database_type)
    assert factory is (asyncio.SelectorEventLoop if selector else None)
    assert ("512-socket" in caplog.text) is selector


@pytest.mark.skipif(sys.platform != "win32", reason="Windows event loop regression")
@pytest.mark.parametrize("entrypoint", ["server", "cli"])
def test_sqlite_entrypoints_use_proactor(monkeypatch, entrypoint):
    loops = []

    async def check(*args):
        loops.append(asyncio.get_running_loop())

    container = SimpleNamespace(database_type="sqlite")
    if entrypoint == "server":
        module = importlib.import_module("agent.bootstrap.app")
        monkeypatch.setattr(module, "_get_default_container", lambda: container)
        monkeypatch.setattr(module, "main", check)
        module.run()
    else:
        module = importlib.import_module("agent.delivery.cli.repl")
        monkeypatch.setattr(module, "CLIRuntime", lambda: SimpleNamespace(container=container))
        monkeypatch.setattr(module, "_run_repl_async", check)
        module.run_repl()
    assert len(loops) == 1
    assert isinstance(loops[0], asyncio.ProactorEventLoop)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows socket limit regression")
def test_default_windows_loop_handles_more_than_512_connections():
    async def check():
        assert isinstance(asyncio.get_running_loop(), asyncio.ProactorEventLoop)
        accepted = []
        clients = []
        all_accepted = asyncio.Event()

        def connected(reader, writer):
            accepted.append(writer)
            if len(accepted) == 520:
                all_accepted.set()

        server = await asyncio.start_server(connected, "127.0.0.1", 0, backlog=1024)
        port = server.sockets[0].getsockname()[1]

        async def connect():
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            clients.append(writer)

        try:
            await asyncio.wait_for(asyncio.gather(*(connect() for _ in range(520))), 15)
            await asyncio.wait_for(all_accepted.wait(), 5)
            assert len(clients) == 520
            await asyncio.sleep(0)
        finally:
            server.close()
            for writer in [*clients, *accepted]:
                writer.close()
            await asyncio.wait_for(asyncio.gather(
                *(writer.wait_closed() for writer in [*clients, *accepted]), return_exceptions=True,
            ), 5)
            await asyncio.wait_for(server.wait_closed(), 5)

    asyncio.run(check(), loop_factory=event_loop.get_event_loop_factory("sqlite"))
