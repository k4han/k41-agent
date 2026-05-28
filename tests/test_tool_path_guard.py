from types import SimpleNamespace

import pytest

import agent.modules.tools.langchain.file_tools.list_files as list_files_module
import agent.modules.tools.langchain.file_tools.read_file as read_file_module
import agent.modules.tools.langchain.file_tools.write_file as write_file_module
import agent.modules.tools.langchain.shell_tools.run_bash as run_bash_module
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


def test_read_file_blocks_parent_traversal(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("top-secret", encoding="utf-8")

    result = read_file_module.read_file.func(
        file_path="../secret.txt",
        runtime=_runtime(str(sandbox)),
    )

    assert "Path escapes working directory" in result


def test_write_file_blocks_absolute_path_outside_working_dir(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside.txt"

    result = write_file_module.write_file.func(
        file_path=str(outside),
        content="should not be written",
        runtime=_runtime(str(sandbox)),
    )

    assert "Path escapes working directory" in result
    assert not outside.exists()


def test_list_files_blocks_parent_traversal(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    result = list_files_module.list_files.func(
        runtime=_runtime(str(sandbox)),
        sub_dir="..",
    )

    assert "Path escapes working directory" in result


def test_run_bash_uses_safe_resolved_working_dir(monkeypatch, tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    captured: dict[str, object] = {}

    class FakeBackend:
        def execute(self, command: str, *, timeout: int = 30, max_output_chars=None):
            captured["command"] = command
            captured["timeout"] = timeout
            captured["max_output_chars"] = max_output_chars
            return SimpleNamespace(output="ok")

    monkeypatch.setattr(run_bash_module, "get_backend", lambda runtime: FakeBackend())

    result = run_bash_module.run_bash.func(
        command="echo ok",
        runtime=_runtime(str(sandbox)),
    )

    assert result == "ok"
    assert captured["command"] == "echo ok"
    assert captured["timeout"] == 30
    assert captured["max_output_chars"] is None
