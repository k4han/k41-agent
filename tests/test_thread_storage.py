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

    assert ".k41-agent/" not in root_listing
    assert not (workspace / ".k41-agent").exists()


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

    assert "[OK] Wrote file" in write_result
    assert read_result == "hello needle\n"
    assert "[OK] Wrote file" in edit_result
    assert ".k41-agent/assets/note.txt" in glob_result
    assert "updated needle" in grep_result
    assert (workspace / ".k41-agent" / "assets" / "note.txt").exists()


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
        file_path=".k41-agent/../../outside.txt",
        content="secret",
        runtime=runtime,
    )

    assert "Path escapes working directory" in result
    assert not (workspace.parent / "outside.txt").exists()


def test_ensure_git_exclude_adds_pattern_when_git_dir_exists(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    git_dir = workspace / ".git"
    git_dir.mkdir()

    thread_storage.ensure_git_exclude(workspace, ".k41-agent/")

    exclude_file = git_dir / "info" / "exclude"
    assert exclude_file.exists()
    content = exclude_file.read_text(encoding="utf-8")
    assert ".k41-agent/" in content

    # Calling again should not duplicate
    thread_storage.ensure_git_exclude(workspace, ".k41-agent/")
    content_again = exclude_file.read_text(encoding="utf-8")
    assert content_again.count(".k41-agent/") == 1


def test_dual_tier_sync_hydrate_and_sync_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage_base = tmp_path / "storage"
    monkeypatch.setattr(thread_storage, "THREAD_STORAGE_BASE_DIR", storage_base)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace_key = "test-ws-sync"

    # Ingest a file to persistent storage
    thread_storage.ingest_attachment_file(
        filename="sales.xlsx",
        content_bytes=b"excel_dummy_bytes",
        workspace_scope=workspace_key,
    )

    # Hydrate into workspace
    thread_storage.hydrate_workspace_storage(workspace, workspace_key)

    ws_file = workspace / ".k41-agent" / "uploads" / "sales.xlsx"
    assert ws_file.exists()
    assert ws_file.read_bytes() == b"excel_dummy_bytes"

    # Agent creates a new file in workspace
    new_report = workspace / ".k41-agent" / "scratchpad" / "report.pdf"
    new_report.parent.mkdir(parents=True)
    new_report.write_bytes(b"pdf_content")

    # Sync back to persistent storage
    thread_storage.sync_back_workspace_storage(workspace, workspace_key)

    storage_report = storage_base / thread_storage.sanitize_workspace_key(workspace_key) / "scratchpad" / "report.pdf"
    assert storage_report.exists()
    assert storage_report.read_bytes() == b"pdf_content"


def test_normalize_chat_attachments_with_file_attachment(tmp_path: Path) -> None:
    import base64
    from agent.modules.agent_runtime.runner import _normalize_chat_attachments, _make_user_message

    raw_data = b"binary-excel-data-sample"
    b64 = base64.b64encode(raw_data).decode("ascii")

    attachments = [
        {
            "name": "sales.xlsx",
            "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "kind": "file",
            "base64": b64,
        }
    ]

    normalized = _normalize_chat_attachments(attachments)
    assert len(normalized) == 1
    assert normalized[0]["name"] == "sales.xlsx"
    assert normalized[0]["kind"] == "file"
    assert normalized[0]["size"] == len(raw_data)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    msg = _make_user_message("Please analyze", attachments, workspace=str(workspace))
    assert isinstance(msg.content, list)
    text_blocks = [b["text"] for b in msg.content if b["type"] == "text"]
    assert any(".k41-agent/uploads/sales.xlsx" in t for t in text_blocks)

    # Ingestion test
    saved_file = workspace / ".k41-agent" / "uploads" / "sales.xlsx"
    assert saved_file.exists()
    assert saved_file.read_bytes() == raw_data
