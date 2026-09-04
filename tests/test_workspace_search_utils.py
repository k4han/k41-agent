from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
import sys

from agent.modules.workspaces import search_utils
from agent.modules.workspaces.constants import IGNORED_DIR_NAMES


def _run_sandbox_glob_script(
    *,
    root: str,
    target: str,
    pattern: str,
    include_dirs: bool = False,
    limit: int = 501,
) -> list[str]:
    old_argv = sys.argv
    sys.argv = [
        "sandbox-glob",
        root,
        target,
        pattern,
        "1" if include_dirs else "0",
        str(limit),
        json.dumps(sorted(IGNORED_DIR_NAMES)),
    ]
    output = StringIO()
    try:
        with redirect_stdout(output):
            try:
                exec(search_utils.SANDBOX_GLOB_SCRIPT, {})
            except SystemExit as exc:
                if exc.code not in (0, None):
                    raise
    finally:
        sys.argv = old_argv
    return output.getvalue().splitlines()


def test_sandbox_glob_script_filters_inside_sandbox(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
    nested = root / "pkg"
    nested.mkdir()
    (nested / "inner.py").write_text("x\n", encoding="utf-8")
    ignored = root / "node_modules"
    ignored.mkdir()
    (ignored / "skip.py").write_text("x\n", encoding="utf-8")

    result = _run_sandbox_glob_script(
        root=str(root),
        target=str(root),
        pattern="**/*.py",
    )

    assert result == ["main.py", "pkg/inner.py"]


def test_sandbox_glob_script_can_include_directories(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "src").mkdir()

    result = _run_sandbox_glob_script(
        root=str(root),
        target=str(root),
        pattern="src",
        include_dirs=True,
    )

    assert result == ["src/"]


def test_sandbox_glob_script_reports_missing_directory(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()

    result = _run_sandbox_glob_script(
        root=str(root),
        target=str(root / "missing"),
        pattern="*.py",
    )

    assert result == [search_utils.DIRECTORY_NOT_FOUND_MESSAGE]


def test_expand_brace_patterns_basic():
    assert search_utils.expand_brace_patterns("**/*.{py,md}") == [
        "**/*.py",
        "**/*.md",
    ]


def test_expand_brace_patterns_nested():
    assert search_utils.expand_brace_patterns("x{b,{c,d}}y") == [
        "xby",
        "xcy",
        "xdy",
    ]


def test_expand_brace_patterns_unclosed_and_no_comma():
    assert search_utils.expand_brace_patterns("a{b,c") == ["a{b,c"]
    assert search_utils.expand_brace_patterns("nocomma{a}b") == ["nocomma{a}b"]


def test_compile_glob_pattern_with_braces():
    pattern_regex = search_utils.compile_glob_pattern("**/*.{py,md}")
    assert pattern_regex.match("a.py")
    assert pattern_regex.match("b.md")
    assert pattern_regex.match("sub/c.py")
    assert not pattern_regex.match("d.txt")


def test_match_include_pattern_with_braces():
    assert search_utils.match_include_pattern("a.py", "*.{py,md}")
    assert search_utils.match_include_pattern("b.md", "*.{py,md}")
    assert not search_utils.match_include_pattern("c.txt", "*.{py,md}")
    assert search_utils.match_include_pattern("anything", None)


def test_build_sandbox_grep_command_expands_include():
    command = search_utils.build_sandbox_grep_command(
        root="/root",
        target="/root",
        relative_path=".",
        pattern="needle",
        include="*.{py,md}",
        case_insensitive=False,
        max_results=100,
    )
    assert command.count("--include=") == 2
    assert "'*.py'" in command or '"*.py"' in command or "*.py" in command
    assert "'*.md'" in command or '"*.md"' in command or "*.md" in command


def test_sandbox_glob_script_matches_local_compile_on_braces(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "main.py").write_text("x\n", encoding="utf-8")
    (root / "readme.md").write_text("x\n", encoding="utf-8")
    (root / "notes.txt").write_text("x\n", encoding="utf-8")
    nested = root / "pkg"
    nested.mkdir()
    (nested / "inner.py").write_text("x\n", encoding="utf-8")

    pattern = "**/*.{py,md}"
    sandbox_result = _run_sandbox_glob_script(
        root=str(root),
        target=str(root),
        pattern=pattern,
    )

    pattern_regex = search_utils.compile_glob_pattern(pattern)
    expected: list[str] = []
    for current_root, _dirs, files in os.walk(str(root)):
        for filename in files:
            full = os.path.join(current_root, filename)
            rel_path = os.path.relpath(full, str(root)).replace(os.sep, "/")
            if search_utils.match_glob_path(pattern_regex, rel_path, rel_path, False):
                expected.append(rel_path)
    assert sorted(sandbox_result) == sorted(expected)
    assert sorted(sandbox_result) == ["main.py", "pkg/inner.py", "readme.md"]


def test_render_sandbox_grep_output_limits_results():
    output = "\n".join(
        [
            "src/a.py:1:     needle one",
            "src/b.py:2: needle two",
            "src/c.py:3: needle three",
        ]
    )

    result = search_utils.render_sandbox_grep_output(output, max_results=2)

    assert "src/a.py:1: needle one" in result
    assert "src/b.py:2: needle two" in result
    assert "src/c.py" not in result
    assert "[truncated at 2 results]" in result
