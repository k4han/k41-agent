from pathlib import Path
from types import SimpleNamespace

import pytest

import agent.modules.tools.builtin.filesystem.edit_file as edit_file_module
import agent.modules.tools.builtin.filesystem.glob as glob_module
import agent.modules.tools.builtin.filesystem.grep as grep_module
import agent.modules.tools.builtin.filesystem.list_dir as list_dir_module
import agent.modules.tools.builtin.filesystem.read_file as read_file_module
import agent.modules.tools.builtin.filesystem.write_file as write_file_module
import agent.modules.tools.runtime.thread_storage as thread_storage


def _runtime(working_dir: str, thread_id: str = "platform_user_channel") -> SimpleNamespace:
    return SimpleNamespace(
        context={"working_dir": working_dir},
        config={"configurable": {"thread_id": thread_id}},
    )


def test_workspace_storage_root_sanitizes_windows_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(thread_storage, "THREAD_STORAGE_BASE_DIR", tmp_path)

    workspace_key = 'CON:bad<id>|trail. '

    root = thread_storage.workspace_storage_root(workspace_key)

    assert root.parent == tmp_path
    assert ":" not in root.name
    assert "<" not in root.name
    assert "|" not in root.name
    assert not root.name.endswith((" ", "."))
    assert thread_storage.sanitize_workspace_key("CON").startswith("workspace-CON-")


@pytest.mark.asyncio
async def test_filesystem_tools_expose_workspace_storage_mount(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(thread_storage, "THREAD_STORAGE_BASE_DIR", tmp_path / "storage")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = _runtime(str(workspace))

    root_listing = await list_dir_module.list_dir.coroutine(runtime=runtime, path="")
    mount_listing = await list_dir_module.list_dir.coroutine(runtime=runtime, path=".k41-agent")

    assert ".k41-agent/" in root_listing
    assert "generated-images/" in mount_listing
    assert "assets/" in mount_listing
    assert "memory/" in mount_listing


@pytest.mark.asyncio
async def test_filesystem_tools_read_write_edit_search_workspace_storage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(thread_storage, "THREAD_STORAGE_BASE_DIR", tmp_path / "storage")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = _runtime(str(workspace), "chat_user:sub:worker:abcd1234")

    write_result = await write_file_module.write_file.coroutine(
        file_path=".k41-agent/assets/note.txt",
        content="hello needle\n",
        runtime=runtime,
    )
    read_result = await read_file_module.read_file.coroutine(
        file_path=".k41-agent/assets/note.txt",
        runtime=runtime,
    )
    edit_result = await edit_file_module.edit_file.coroutine(
        file_path=".k41-agent/assets/note.txt",
        old_string="hello",
        new_string="updated",
        runtime=runtime,
    )
    glob_result = await glob_module.glob.coroutine(
        pattern="*.txt",
        path=".k41-agent/assets",
        runtime=runtime,
    )
    grep_result = await grep_module.grep.coroutine(
        pattern="needle",
        path=".k41-agent",
        runtime=runtime,
    )

    assert write_result == "[OK] Wrote file: .k41-agent/assets/note.txt"
    assert read_result == "hello needle\n"
    assert edit_result == "[OK] Wrote file: .k41-agent/assets/note.txt"
    assert ".k41-agent/assets/note.txt" in glob_result
    assert ".k41-agent/assets/note.txt:1: updated needle" in grep_result
    assert not (workspace / ".k41-agent").exists()


@pytest.mark.asyncio
async def test_workspace_storage_is_shared_by_workspace_not_thread(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(thread_storage, "THREAD_STORAGE_BASE_DIR", tmp_path / "storage")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    await write_file_module.write_file.coroutine(
        file_path=".k41-agent/memory/note.txt",
        content="shared",
        runtime=_runtime(str(workspace), "thread-one"),
    )
    read_result = await read_file_module.read_file.coroutine(
        file_path=".k41-agent/memory/note.txt",
        runtime=_runtime(str(workspace), "thread-two"),
    )

    assert read_result == "shared"


@pytest.mark.asyncio
async def test_workspace_storage_is_isolated_between_workspaces(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(thread_storage, "THREAD_STORAGE_BASE_DIR", tmp_path / "storage")
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()

    await write_file_module.write_file.coroutine(
        file_path=".k41-agent/memory/note.txt",
        content="first",
        runtime=_runtime(str(first_workspace), "same-thread"),
    )
    read_result = await read_file_module.read_file.coroutine(
        file_path=".k41-agent/memory/note.txt",
        runtime=_runtime(str(second_workspace), "same-thread"),
    )

    assert "[error] not_found" in read_result


@pytest.mark.asyncio
async def test_workspace_storage_mount_blocks_path_traversal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage_base = tmp_path / "storage"
    monkeypatch.setattr(thread_storage, "THREAD_STORAGE_BASE_DIR", storage_base)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = _runtime(str(workspace))

    result = await write_file_module.write_file.coroutine(
        file_path=".k41-agent/../outside.txt",
        content="secret",
        runtime=runtime,
    )

    assert "Path escapes working directory" in result
    assert not (storage_base / "outside.txt").exists()
