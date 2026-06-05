from types import SimpleNamespace

import pytest

import agent.modules.tools.builtin.filesystem.glob as glob_module
import agent.modules.tools.builtin.filesystem.grep as grep_module


def _runtime(working_dir: str) -> SimpleNamespace:
    return SimpleNamespace(context={"working_dir": working_dir})


class TestGlobTool:
    @pytest.mark.asyncio
    async def test_glob_finds_matching_files(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "main.py").write_text("print('hi')", encoding="utf-8")
        (sandbox / "helper.py").write_text("# helper", encoding="utf-8")
        (sandbox / "README.md").write_text("docs", encoding="utf-8")
        nested = sandbox / "pkg"
        nested.mkdir()
        (nested / "inner.py").write_text("x", encoding="utf-8")

        result = await glob_module.glob.coroutine(
            pattern="*.py",
            runtime=_runtime(str(sandbox)),
        )

        assert "main.py" in result
        assert "helper.py" in result
        assert "README.md" not in result

    @pytest.mark.asyncio
    async def test_glob_recurses_into_nested_dirs(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        deep = sandbox / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "target.py").write_text("x", encoding="utf-8")

        result = await glob_module.glob.coroutine(
            pattern="target.py",
            runtime=_runtime(str(sandbox)),
        )

        assert "a/b/c/target.py" in result

    @pytest.mark.asyncio
    async def test_glob_skips_ignored_directories(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "keep.py").write_text("x", encoding="utf-8")
        ignored = sandbox / "node_modules"
        ignored.mkdir()
        (ignored / "skip.py").write_text("x", encoding="utf-8")
        pycache = sandbox / "__pycache__"
        pycache.mkdir()
        (pycache / "skip.py").write_text("x", encoding="utf-8")

        result = await glob_module.glob.coroutine(
            pattern="*.py",
            runtime=_runtime(str(sandbox)),
        )

        assert "keep.py" in result
        assert "node_modules" not in result
        assert "__pycache__" not in result

    @pytest.mark.asyncio
    async def test_glob_returns_empty_message_when_no_match(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "file.txt").write_text("x", encoding="utf-8")

        result = await glob_module.glob.coroutine(
            pattern="*.py",
            runtime=_runtime(str(sandbox)),
        )

        assert result == "(No matches)"

    @pytest.mark.asyncio
    async def test_glob_reports_missing_directory(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()

        result = await glob_module.glob.coroutine(
            pattern="*.py",
            runtime=_runtime(str(sandbox)),
            path="missing",
        )

        assert result == "(Directory not found)"

    @pytest.mark.asyncio
    async def test_glob_blocks_parent_traversal(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox.parent / "secret.py").write_text("x", encoding="utf-8")

        result = await glob_module.glob.coroutine(
            pattern="*.py",
            runtime=_runtime(str(sandbox)),
            path="..",
        )

        assert "[error]" in result
        assert "Path escapes" in result

    @pytest.mark.asyncio
    async def test_glob_rejects_empty_pattern(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()

        result = await glob_module.glob.coroutine(
            pattern="",
            runtime=_runtime(str(sandbox)),
        )

        assert "[error] invalid_input" in result

    @pytest.mark.asyncio
    async def test_glob_include_dirs(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "src").mkdir()
        (sandbox / "src").joinpath("main.py").write_text("x", encoding="utf-8")

        result = await glob_module.glob.coroutine(
            pattern="src",
            runtime=_runtime(str(sandbox)),
            include_dirs=True,
        )

        assert "src/" in result


class TestGrepTool:
    @pytest.mark.asyncio
    async def test_grep_finds_matching_lines(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "a.py").write_text(
            "def hello():\n    return 42\n", encoding="utf-8"
        )
        (sandbox / "b.py").write_text(
            "class Greeter:\n    name = 'Hello'\n", encoding="utf-8"
        )

        result = await grep_module.grep.coroutine(
            pattern="Hello",
            runtime=_runtime(str(sandbox)),
        )

        assert "b.py:2" in result
        assert "a.py" not in result
        assert "[Matches in" in result

    @pytest.mark.asyncio
    async def test_grep_respects_include_glob(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "a.py").write_text("hello\n", encoding="utf-8")
        (sandbox / "a.md").write_text("hello\n", encoding="utf-8")

        result = await grep_module.grep.coroutine(
            pattern="hello",
            runtime=_runtime(str(sandbox)),
            include="*.py",
        )

        assert "a.py" in result
        assert "a.md" not in result

    @pytest.mark.asyncio
    async def test_grep_case_insensitive(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "a.txt").write_text("Hello\nworld\nhello\n", encoding="utf-8")

        result_default = await grep_module.grep.coroutine(
            pattern="hello",
            runtime=_runtime(str(sandbox)),
        )
        assert "a.txt:3" in result_default
        assert "a.txt:1" not in result_default

        result_ci = await grep_module.grep.coroutine(
            pattern="hello",
            runtime=_runtime(str(sandbox)),
            case_insensitive=True,
        )
        assert "a.txt:1" in result_ci
        assert "a.txt:3" in result_ci

    @pytest.mark.asyncio
    async def test_grep_returns_no_match_message(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "a.txt").write_text("abc\n", encoding="utf-8")

        result = await grep_module.grep.coroutine(
            pattern="definitely-not-present",
            runtime=_runtime(str(sandbox)),
        )

        assert "(No matches" in result

    @pytest.mark.asyncio
    async def test_grep_reports_missing_directory(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()

        result = await grep_module.grep.coroutine(
            pattern="abc",
            runtime=_runtime(str(sandbox)),
            path="missing",
        )

        assert result == "(Directory not found)"

    @pytest.mark.asyncio
    async def test_grep_blocks_parent_traversal(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox.parent / "secret.txt").write_text("needle", encoding="utf-8")

        result = await grep_module.grep.coroutine(
            pattern="needle",
            runtime=_runtime(str(sandbox)),
            path="..",
        )

        assert "[error]" in result
        assert "Path escapes" in result

    @pytest.mark.asyncio
    async def test_grep_rejects_empty_pattern(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()

        result = await grep_module.grep.coroutine(
            pattern="",
            runtime=_runtime(str(sandbox)),
        )

        assert "[error] invalid_input" in result

    @pytest.mark.asyncio
    async def test_grep_skips_ignored_directories(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "a.py").write_text("needle\n", encoding="utf-8")
        ignored = sandbox / "node_modules"
        ignored.mkdir()
        (ignored / "skip.py").write_text("needle\n", encoding="utf-8")

        result = await grep_module.grep.coroutine(
            pattern="needle",
            runtime=_runtime(str(sandbox)),
        )

        assert "a.py" in result
        assert "skip.py" not in result

    @pytest.mark.asyncio
    async def test_grep_caps_max_results(self, tmp_path):
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        lines = "\n".join(f"match line {i}" for i in range(20)) + "\n"
        (sandbox / "a.txt").write_text(lines, encoding="utf-8")

        result = await grep_module.grep.coroutine(
            pattern="match line",
            runtime=_runtime(str(sandbox)),
            max_results=5,
        )

        assert "[truncated at 5 results]" in result
