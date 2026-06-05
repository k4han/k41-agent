from types import SimpleNamespace

import pytest

import agent.modules.tools.builtin.filesystem.edit_file as edit_file_module


def _runtime(working_dir: str) -> SimpleNamespace:
    return SimpleNamespace(context={"working_dir": working_dir})


@pytest.mark.asyncio
async def test_edit_file_blocks_parent_traversal(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    result = await edit_file_module.edit_file.coroutine(
        file_path="../secret.txt",
        old_string="a",
        new_string="b",
        runtime=_runtime(str(sandbox)),
    )

    assert "[error]" in result
    assert "Path escapes working directory" in result


@pytest.mark.asyncio
async def test_edit_file_replaces_first_occurrence(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    target = sandbox / "doc.txt"
    target.write_text("hello world\ngoodbye universe\n", encoding="utf-8")

    result = await edit_file_module.edit_file.coroutine(
        file_path="doc.txt",
        old_string="hello world",
        new_string="hi world",
        runtime=_runtime(str(sandbox)),
    )

    assert "[OK]" in result
    assert target.read_text(encoding="utf-8") == "hi world\ngoodbye universe\n"


@pytest.mark.asyncio
async def test_edit_file_replace_all_replaces_every_occurrence(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    target = sandbox / "doc.txt"
    target.write_text("hello world\nhello universe\n", encoding="utf-8")

    await edit_file_module.edit_file.coroutine(
        file_path="doc.txt",
        old_string="hello",
        new_string="hi",
        replace_all=True,
        runtime=_runtime(str(sandbox)),
    )

    assert target.read_text(encoding="utf-8") == "hi world\nhi universe\n"


@pytest.mark.asyncio
async def test_edit_file_ambiguous_match_is_rejected(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    target = sandbox / "doc.txt"
    target.write_text("hello world\nhello universe\n", encoding="utf-8")

    result = await edit_file_module.edit_file.coroutine(
        file_path="doc.txt",
        old_string="hello",
        new_string="hi",
        runtime=_runtime(str(sandbox)),
    )

    assert "[error] invalid_input" in result
    assert "matches 2 locations" in result
    assert target.read_text(encoding="utf-8") == "hello world\nhello universe\n"


@pytest.mark.asyncio
async def test_edit_file_missing_string_reports_not_found(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    target = sandbox / "doc.txt"
    target.write_text("hello world\n", encoding="utf-8")

    result = await edit_file_module.edit_file.coroutine(
        file_path="doc.txt",
        old_string="absent",
        new_string="hi",
        runtime=_runtime(str(sandbox)),
    )

    assert "[error] not_found" in result


@pytest.mark.asyncio
async def test_edit_file_missing_file_reports_not_found(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    result = await edit_file_module.edit_file.coroutine(
        file_path="missing.txt",
        old_string="a",
        new_string="b",
        runtime=_runtime(str(sandbox)),
    )

    assert "[error] not_found" in result


@pytest.mark.asyncio
async def test_edit_file_empty_old_string_is_rejected(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    target = sandbox / "doc.txt"
    target.write_text("hello", encoding="utf-8")

    result = await edit_file_module.edit_file.coroutine(
        file_path="doc.txt",
        old_string="",
        new_string="x",
        runtime=_runtime(str(sandbox)),
    )

    assert "[error] invalid_input" in result
    assert target.read_text(encoding="utf-8") == "hello"
