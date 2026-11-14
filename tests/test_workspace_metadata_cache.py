import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import agent.modules.workspaces.metadata_cache as cache_module
from agent.modules.workspaces.refs import WorkspaceRef


@pytest.fixture(autouse=True)
def cache_clock(monkeypatch):
    clock = SimpleNamespace(now=10.0)
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    cache_module.clear_workspace_metadata_cache()
    yield clock
    cache_module.clear_workspace_metadata_cache()


@pytest.fixture
def workspace():
    return WorkspaceRef(backend="daytona", locator="sandbox-1")


def _store(workspace, path, value, *, root="/workspace"):
    cache_module.set_workspace_metadata_cache(
        workspace, value, root=root, operation="tree", parts=(path,)
    )


def _load(workspace, path, *, root="/workspace"):
    return cache_module.get_workspace_metadata_cache(
        workspace, root=root, operation="tree", parts=(path,)
    )


def test_default_capacity_is_bounded(workspace):
    for index in range(1025):
        _store(workspace, str(index), index)

    assert len(cache_module._metadata_cache) == 1024
    assert _load(workspace, "0") == (False, None)
    assert _load(workspace, "1024") == (True, 1024)


@pytest.mark.parametrize("refresh", ["read", "write"])
def test_capacity_evicts_least_recently_used_entry(workspace, monkeypatch, refresh):
    monkeypatch.setattr(cache_module, "METADATA_CACHE_MAX_ENTRIES", 2)
    _store(workspace, "first", "original")
    _store(workspace, "second", "other")

    if refresh == "read":
        assert _load(workspace, "first") == (True, "original")
    else:
        _store(workspace, "first", "replacement")
    _store(workspace, "third", "new")

    assert _load(workspace, "second") == (False, None)
    expected = "original" if refresh == "read" else "replacement"
    assert _load(workspace, "first") == (True, expected)
    assert _load(workspace, "third") == (True, "new")


def test_overwriting_at_capacity_keeps_other_entries(workspace, monkeypatch):
    monkeypatch.setattr(cache_module, "METADATA_CACHE_MAX_ENTRIES", 2)
    _store(workspace, "first", "original")
    _store(workspace, "second", "other")
    _store(workspace, "first", "replacement")

    assert _load(workspace, "first") == (True, "replacement")
    assert _load(workspace, "second") == (True, "other")
    assert len(cache_module._metadata_cache) == 2


def test_reads_preserve_ttl_and_remove_expired_entries(workspace, cache_clock):
    _store(workspace, "src", {"entries": []})
    cache_clock.now = 10.5
    assert _load(workspace, "src") == (True, {"entries": []})

    cache_clock.now = 11.25
    assert _load(workspace, "src") == (False, None)
    assert not cache_module._metadata_cache


def test_stored_and_returned_metadata_are_independent_copies(workspace):
    metadata = {"entries": [{"name": "app.py"}]}
    _store(workspace, "src", metadata)
    metadata["entries"][0]["name"] = "changed.py"

    found, cached = _load(workspace, "src")
    assert found
    assert cached == {"entries": [{"name": "app.py"}]}
    cached["entries"].clear()
    assert _load(workspace, "src") == (True, {"entries": [{"name": "app.py"}]})


@pytest.mark.parametrize("root", [None, "/workspace"])
def test_invalidation_preserves_other_workspaces_and_roots(workspace, root):
    other_backend = WorkspaceRef(backend="modal", locator=workspace.locator)
    other_workspace = WorkspaceRef(backend=workspace.backend, locator="sandbox-2")
    _store(workspace, "src", "target")
    _store(workspace, "src", "other root", root="/other")
    _store(other_backend, "src", "other backend")
    _store(other_workspace, "src", "other workspace")

    cache_module.invalidate_workspace_metadata_cache(workspace, root=root)

    assert _load(workspace, "src") == (False, None)
    expected = (False, None) if root is None else (True, "other root")
    assert _load(workspace, "src", root="/other") == expected
    assert _load(other_backend, "src") == (True, "other backend")
    assert _load(other_workspace, "src") == (True, "other workspace")


def test_expired_lookup_does_not_delete_concurrent_replacement(workspace, monkeypatch):
    _store(workspace, "src", "old")
    reader_in_clock = threading.Event()
    release_reader = threading.Event()
    writer_started = threading.Event()
    writer_finished = threading.Event()
    reader_context = threading.local()

    def monotonic():
        if getattr(reader_context, "is_reader", False):
            reader_in_clock.set()
            assert release_reader.wait(timeout=5)
        return 12.0

    monkeypatch.setattr(cache_module, "time", SimpleNamespace(monotonic=monotonic))

    def read_expired():
        reader_context.is_reader = True
        return _load(workspace, "src")

    def replace():
        writer_started.set()
        _store(workspace, "src", "new")
        writer_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        reader = executor.submit(read_expired)
        try:
            assert reader_in_clock.wait(timeout=5)
            writer = executor.submit(replace)
            assert writer_started.wait(timeout=5)
            writer_finished.wait(timeout=0.1)
        finally:
            release_reader.set()
        assert reader.result(timeout=5) == (False, None)
        writer.result(timeout=5)

    assert _load(workspace, "src") == (True, "new")


def test_concurrent_reads_writes_invalidation_and_clear(workspace, monkeypatch):
    monkeypatch.setattr(cache_module, "METADATA_CACHE_MAX_ENTRIES", 8)
    start = threading.Barrier(4)

    def access_entries(worker_id):
        start.wait(timeout=5)
        for index in range(100):
            path = f"{worker_id}:{index}"
            _store(workspace, path, index)
            assert _load(workspace, path) in ((False, None), (True, index))
            if index % 11 == 0:
                cache_module.clear_workspace_metadata_cache()
            elif index % 7 == 0:
                cache_module.invalidate_workspace_metadata_cache(workspace)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(access_entries, range(4)))

    assert len(cache_module._metadata_cache) <= 8
    cache_module.clear_workspace_metadata_cache()
    assert _load(workspace, "src") == (False, None)
    _store(workspace, "src", "reused")
    assert _load(workspace, "src") == (True, "reused")
