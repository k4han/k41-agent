from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
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

    assert result == ["pkg/inner.py"]


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
