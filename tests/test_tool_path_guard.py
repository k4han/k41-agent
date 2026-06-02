from types import SimpleNamespace

import pytest

import agent.modules.tools.langchain.file_tools.list_files as list_files_module
import agent.modules.tools.langchain.file_tools.read_file as read_file_module
import agent.modules.tools.langchain.file_tools.write_file as write_file_module
from agent.modules.tools.runtime.path_guard import resolve_safe_path


def _runtime(working_dir: str) -> SimpleNamespace:
    return SimpleNamespace(context={"working_dir": working_dir})


def test_resolve_safe_path_allows_path_inside_working_dir(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    resolved = resolve_safe_path(str(sandbox), "nested/file.txt")

    assert resolved == str((sandbox / "nested" / "file.txt").resolve())


def test_resolve_safe_path_allows_absolute_path_inside_working_dir(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    target = sandbox / "nested" / "file.txt"

    resolved = resolve_safe_path(str(sandbox), str(target))

    assert resolved == str(target.resolve())


def test_resolve_safe_path_rejects_parent_traversal(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    with pytest.raises(ValueError, match="Path escapes working directory"):
        resolve_safe_path(str(sandbox), "../secret.txt")


def test_resolve_safe_path_rejects_absolute_path_outside_working_dir(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside.txt"

    with pytest.raises(ValueError, match="Path escapes working directory"):
        resolve_safe_path(str(sandbox), str(outside))


@pytest.mark.asyncio
async def test_read_file_blocks_parent_traversal(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("top-secret", encoding="utf-8")

    result = await read_file_module.read_file.coroutine(
        file_path="../secret.txt",
        runtime=_runtime(str(sandbox)),
    )

    assert "Path escapes working directory" in result


@pytest.mark.asyncio
async def test_write_file_blocks_absolute_path_outside_working_dir(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside.txt"

    result = await write_file_module.write_file.coroutine(
        file_path=str(outside),
        content="should not be written",
        runtime=_runtime(str(sandbox)),
    )

    assert "Path escapes working directory" in result
    assert not outside.exists()


@pytest.mark.asyncio
async def test_list_files_blocks_parent_traversal(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    result = await list_files_module.list_files.coroutine(
        runtime=_runtime(str(sandbox)),
        sub_dir="..",
    )

    assert "Path escapes working directory" in result

