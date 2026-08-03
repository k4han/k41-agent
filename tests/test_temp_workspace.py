import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent.modules.workspaces.service as service_module
from agent.modules.workspaces import (
    cleanup_orphaned_temp_workspaces,
    create_temp_workspace,
    delete_thread_workspace,
    is_temp_workspace,
    resolve_workspace_ref,
)


@pytest.fixture(autouse=True)
def _temp_workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    temp_root = tmp_path / ".temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(service_module, "temp_workspace_root", lambda: temp_root)
    return temp_root


def test_create_temp_workspace_creates_hidden_directory(_temp_workspace_root: Path):
    ref = asyncio.run(create_temp_workspace(None))

    assert ref.backend == "local"
    target = Path(ref.locator)
    assert target.is_dir()
    assert target.parent == _temp_workspace_root
    assert target.name.startswith("thread-")
    assert bool(ref.metadata.get("temp")) is True


def test_create_temp_workspace_with_thread_id_uses_safe_key(_temp_workspace_root: Path):
    ref = asyncio.run(create_temp_workspace("api/abc def?"))
    assert Path(ref.locator).name == "thread-apiabcdef"


def test_is_temp_workspace_detection():
    temp_ref = asyncio.run(create_temp_workspace("thread-1"))
    assert is_temp_workspace(temp_ref) is True
    assert is_temp_workspace(temp_ref.model_dump()) is True

    plain_ref = resolve_workspace_ref({"backend": "local", "locator": "C:/work/project", "metadata": {}})
    assert is_temp_workspace(plain_ref) is False
    assert is_temp_workspace(None) is False


def test_delete_temp_workspace_directory_removes_only_within_root(
    _temp_workspace_root: Path,
):
    temp_ref = asyncio.run(create_temp_workspace("thread-2"))
    target = Path(temp_ref.locator)
    (target / "notes.txt").write_text("scratch", encoding="utf-8")

    asyncio.run(service_module._delete_temp_workspace_directory(temp_ref.locator))
    assert not target.exists()

    outside = _temp_workspace_root.parent / "outside-project"
    outside.mkdir(parents=True, exist_ok=True)
    asyncio.run(service_module._delete_temp_workspace_directory(str(outside)))
    assert outside.exists()


def test_delete_thread_workspace_cleans_temp_directory(
    _temp_workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    temp_ref = asyncio.run(create_temp_workspace("thread-3"))
    target = Path(temp_ref.locator)
    assert target.exists()

    deleted = []

    async def fake_get_thread_workspace_ref(thread_id: str):
        return temp_ref

    async def fake_lifecycle_manager(*args, **kwargs):
        return None

    class FakeRepository:
        async def delete(self, thread_id: str) -> None:
            deleted.append(thread_id)

    monkeypatch.setattr(service_module, "get_thread_workspace_ref", fake_get_thread_workspace_ref)
    monkeypatch.setattr(service_module, "get_workspace_lifecycle_manager", fake_lifecycle_manager)
    monkeypatch.setattr(
        service_module,
        "get_thread_workspace_repository",
        lambda: FakeRepository(),
    )

    result = asyncio.run(delete_thread_workspace("thread-3"))

    assert result == temp_ref
    assert not target.exists()
    assert deleted == ["thread-3"]


def test_delete_thread_workspace_keeps_regular_workspace_directory(
    _temp_workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    project_dir = _temp_workspace_root.parent / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    workspace = resolve_workspace_ref(
        {"backend": "local", "locator": str(project_dir), "metadata": {}}
    )

    async def fake_get_thread_workspace_ref(thread_id: str):
        return workspace

    async def fake_lifecycle_manager(*args, **kwargs):
        return None

    class FakeRepository:
        async def delete(self, thread_id: str) -> None:
            return None

    monkeypatch.setattr(service_module, "get_thread_workspace_ref", fake_get_thread_workspace_ref)
    monkeypatch.setattr(service_module, "get_workspace_lifecycle_manager", fake_lifecycle_manager)
    monkeypatch.setattr(
        service_module,
        "get_thread_workspace_repository",
        lambda: FakeRepository(),
    )

    asyncio.run(delete_thread_workspace("thread-4"))

    assert project_dir.exists()


def _patch_delete_thread_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    workspace_ref,
    *,
    alive_roots: set[str],
):
    async def fake_get_thread_workspace_ref(thread_id: str):
        return workspace_ref

    async def fake_lifecycle_manager(*args, **kwargs):
        return None

    class FakeRepository:
        async def delete(self, thread_id: str) -> None:
            return None

    import agent.modules.conversations as conversations_module

    async def _list_active_thread_ids(thread_ids):
        return set(alive_roots) & set(thread_ids)

    monkeypatch.setattr(service_module, "get_thread_workspace_ref", fake_get_thread_workspace_ref)
    monkeypatch.setattr(service_module, "get_workspace_lifecycle_manager", fake_lifecycle_manager)
    monkeypatch.setattr(
        service_module,
        "get_thread_workspace_repository",
        lambda: FakeRepository(),
    )
    monkeypatch.setattr(conversations_module, "list_active_thread_ids", _list_active_thread_ids)


def test_delete_subthread_workspace_keeps_directory_when_root_alive(
    _temp_workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    shared_ref = asyncio.run(create_temp_workspace("thread-root"))
    target = Path(shared_ref.locator)
    (target / "notes.txt").write_text("scratch", encoding="utf-8")

    _patch_delete_thread_dependencies(
        monkeypatch,
        shared_ref,
        alive_roots={"thread-root"},
    )

    result = asyncio.run(delete_thread_workspace("thread-root:sub:1"))

    assert result == shared_ref
    assert target.exists()


def test_delete_subthread_workspace_removes_directory_when_root_dead(
    _temp_workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    shared_ref = asyncio.run(create_temp_workspace("thread-root"))
    target = Path(shared_ref.locator)
    assert target.exists()

    _patch_delete_thread_dependencies(
        monkeypatch,
        shared_ref,
        alive_roots=set(),
    )

    result = asyncio.run(delete_thread_workspace("thread-root:sub:1"))

    assert result == shared_ref
    assert not target.exists()


class _FakeTempRepository:
    def __init__(self, records: dict[str, dict]):
        self.records = dict(records)
        self.deleted: list[str] = []

    async def list_by_backend(self, backend: str) -> dict[str, dict]:
        return dict(self.records)

    async def delete(self, thread_id: str) -> bool:
        if thread_id in self.records:
            self.deleted.append(thread_id)
            del self.records[thread_id]
            return True
        return False


def _temp_record(thread_id: str, workspace_ref):
    return {
        "thread_id": thread_id,
        "workspace": workspace_ref.model_dump(),
        "created_at": None,
        "updated_at": None,
    }


def _patch_temp_cleanup_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    repo: _FakeTempRepository,
    *,
    alive_thread_ids: set[str] | None = None,
) -> None:
    monkeypatch.setattr(service_module, "get_thread_workspace_repository", lambda: repo)

    import agent.modules.conversations as conversations_module

    async def _list_active_thread_ids(thread_ids):
        return set(alive_thread_ids or set()) & set(thread_ids)

    monkeypatch.setattr(
        conversations_module,
        "list_active_thread_ids",
        _list_active_thread_ids,
    )


def test_cleanup_removes_dead_temp_thread_records_and_dirs(
    _temp_workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    dead_ref = asyncio.run(create_temp_workspace("thread-dead"))
    repo = _FakeTempRepository(
        {
            "thread-dead": _temp_record("thread-dead", dead_ref),
        }
    )
    _patch_temp_cleanup_dependencies(monkeypatch, repo, alive_thread_ids={"thread-alive"})

    stats = asyncio.run(cleanup_orphaned_temp_workspaces())

    assert stats == {"directories_removed": 1, "records_removed": 1}
    assert not Path(dead_ref.locator).exists()
    assert repo.deleted == ["thread-dead"]


def test_cleanup_removes_orphaned_directories_without_records(
    _temp_workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    orphan = _temp_workspace_root / "thread-orphan"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "scratch.txt").write_text("x", encoding="utf-8")

    repo = _FakeTempRepository({})
    _patch_temp_cleanup_dependencies(monkeypatch, repo)

    stats = asyncio.run(cleanup_orphaned_temp_workspaces())

    assert stats == {"directories_removed": 1, "records_removed": 0}
    assert not orphan.exists()


def test_cleanup_keeps_orphaned_directory_when_thread_alive(
    _temp_workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    live = _temp_workspace_root / "thread-live-session"
    live.mkdir(parents=True, exist_ok=True)
    (live / "scratch.txt").write_text("x", encoding="utf-8")

    repo = _FakeTempRepository({})
    _patch_temp_cleanup_dependencies(
        monkeypatch,
        repo,
        alive_thread_ids={"live-session"},
    )

    stats = asyncio.run(cleanup_orphaned_temp_workspaces())

    assert stats == {"directories_removed": 0, "records_removed": 0}
    assert live.exists()


def test_cleanup_keeps_orphaned_directories_when_alive_check_fails(
    _temp_workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    live = _temp_workspace_root / "thread-session"
    live.mkdir(parents=True, exist_ok=True)
    (live / "scratch.txt").write_text("x", encoding="utf-8")

    repo = _FakeTempRepository({})
    _patch_temp_cleanup_dependencies(monkeypatch, repo)

    import agent.modules.conversations as conversations_module

    async def _boom(thread_ids):
        raise RuntimeError("db down")

    monkeypatch.setattr(conversations_module, "list_active_thread_ids", _boom)

    stats = asyncio.run(cleanup_orphaned_temp_workspaces())

    assert stats == {"directories_removed": 0, "records_removed": 0}
    assert live.exists()


def test_cleanup_keeps_alive_temp_thread_dirs_and_records(
    _temp_workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    alive_ref = asyncio.run(create_temp_workspace("thread-alive"))
    repo = _FakeTempRepository(
        {
            "thread-alive": _temp_record("thread-alive", alive_ref),
        }
    )
    _patch_temp_cleanup_dependencies(monkeypatch, repo, alive_thread_ids={"thread-alive"})

    stats = asyncio.run(cleanup_orphaned_temp_workspaces())

    assert stats == {"directories_removed": 0, "records_removed": 0}
    assert Path(alive_ref.locator).exists()
    assert repo.deleted == []


def test_cleanup_ignores_regular_local_workspace_records(
    _temp_workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    project_dir = _temp_workspace_root.parent / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    regular = resolve_workspace_ref(
        {"backend": "local", "locator": str(project_dir), "metadata": {}}
    )
    repo = _FakeTempRepository(
        {
            "thread-regular": _temp_record("thread-regular", regular),
        }
    )
    _patch_temp_cleanup_dependencies(monkeypatch, repo)

    stats = asyncio.run(cleanup_orphaned_temp_workspaces())

    assert stats == {"directories_removed": 0, "records_removed": 0}
    assert project_dir.exists()
    assert repo.deleted == []


def test_cleanup_keeps_subthread_record_when_root_alive(
    _temp_workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    sub_ref = asyncio.run(create_temp_workspace("thread-root"))
    repo = _FakeTempRepository(
        {
            "thread-root:sub:1": _temp_record("thread-root:sub:1", sub_ref),
        }
    )
    _patch_temp_cleanup_dependencies(
        monkeypatch,
        repo,
        alive_thread_ids={"thread-root"},
    )

    stats = asyncio.run(cleanup_orphaned_temp_workspaces())

    assert stats == {"directories_removed": 0, "records_removed": 0}
    assert Path(sub_ref.locator).exists()
    assert repo.deleted == []


def test_cleanup_removes_subthread_record_when_root_dead(
    _temp_workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    sub_ref = asyncio.run(create_temp_workspace("thread-root"))
    repo = _FakeTempRepository(
        {
            "thread-root:sub:1": _temp_record("thread-root:sub:1", sub_ref),
        }
    )
    _patch_temp_cleanup_dependencies(monkeypatch, repo, alive_thread_ids=set())

    stats = asyncio.run(cleanup_orphaned_temp_workspaces())

    assert stats == {"directories_removed": 1, "records_removed": 1}
    assert not Path(sub_ref.locator).exists()
    assert repo.deleted == ["thread-root:sub:1"]
