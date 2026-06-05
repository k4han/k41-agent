from __future__ import annotations

import json
import shlex

from agent.modules.workspaces.constants import (
    IGNORED_DIR_NAMES,
    MAX_GLOB_RESULTS,
    MAX_GREP_LINE_CHARS,
    MAX_GREP_RESULTS,
)
from agent.modules.workspaces.posix_utils import normalize_posix_path

DIRECTORY_NOT_FOUND_MESSAGE = "(Directory not found)"
NO_MATCHES_MESSAGE = "(No matches)"

SANDBOX_GLOB_SCRIPT = r"""
import fnmatch
import json
import os
import sys

root, target, pattern, include_dirs_raw, limit_raw, ignored_raw = sys.argv[1:7]
include_dirs = include_dirs_raw == "1"
limit = int(limit_raw)
ignored = set(json.loads(ignored_raw))

if not os.path.isdir(target):
    print("(Directory not found)")
    raise SystemExit(0)

count = 0
for current_root, dirs, files in os.walk(target, followlinks=False):
    dirs[:] = sorted(name for name in dirs if name not in ignored)
    entries = [(name, True) for name in dirs]
    entries.extend((name, False) for name in files)
    for name, is_dir in sorted(entries, key=lambda item: (not item[1], item[0].lower())):
        if is_dir and not include_dirs:
            continue
        full_path = os.path.join(current_root, name)
        rel_path = os.path.relpath(full_path, root).replace(os.sep, "/")
        if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(name, pattern):
            print(f"{rel_path}/" if is_dir else rel_path)
            count += 1
            if count >= limit:
                raise SystemExit(0)
""".strip()


def clamp_grep_results(max_results: int) -> int:
    if max_results <= 0:
        return MAX_GREP_RESULTS
    return min(max_results, MAX_GREP_RESULTS)


def build_sandbox_glob_command(
    *,
    root: str,
    target: str,
    pattern: str,
    include_dirs: bool,
) -> str:
    args = [
        root,
        target,
        pattern,
        "1" if include_dirs else "0",
        str(MAX_GLOB_RESULTS + 1),
        json.dumps(sorted(IGNORED_DIR_NAMES)),
    ]
    quoted_args = " ".join(shlex.quote(arg) for arg in args)
    return (
        "python_bin=$(command -v python3 || command -v python); "
        'if [ -z "$python_bin" ]; then exit 127; fi; '
        f'"$python_bin" -c {shlex.quote(SANDBOX_GLOB_SCRIPT)} {quoted_args}'
    )


def build_sandbox_grep_command(
    *,
    root: str,
    target: str,
    relative_path: str,
    pattern: str,
    include: str | None,
    case_insensitive: bool,
    max_results: int,
) -> str:
    effective_max = clamp_grep_results(max_results)
    flags = "-ErnI" + ("i" if case_insensitive else "")
    include_clause = f" --include={shlex.quote(include)}" if include else ""
    excluded = " ".join(
        f"--exclude-dir={shlex.quote(name)}" for name in sorted(IGNORED_DIR_NAMES)
    )
    target_arg = relative_path or "."
    return (
        f"if [ ! -d {shlex.quote(target)} ]; then "
        f"printf '%s\\n' {shlex.quote(DIRECTORY_NOT_FOUND_MESSAGE)}; "
        "else "
        f"cd {shlex.quote(root)} && "
        f"grep {flags}{include_clause} {excluded} -- "
        f"{shlex.quote(pattern)} {shlex.quote(target_arg)} 2>/dev/null "
        f"| head -n {effective_max + 1}; "
        "fi"
    )


def render_sandbox_glob_output(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if lines == [DIRECTORY_NOT_FOUND_MESSAGE]:
        return DIRECTORY_NOT_FOUND_MESSAGE
    if not lines:
        return NO_MATCHES_MESSAGE
    truncated = len(lines) > MAX_GLOB_RESULTS
    lines = lines[:MAX_GLOB_RESULTS]
    rendered = "\n".join(lines)
    if truncated:
        rendered += f"\n...[truncated at {MAX_GLOB_RESULTS} results]"
    return rendered


def render_sandbox_grep_output(output: str, *, max_results: int) -> str:
    effective_max = clamp_grep_results(max_results)
    lines = [line for line in output.splitlines() if line.strip()]
    if lines == [DIRECTORY_NOT_FOUND_MESSAGE]:
        return DIRECTORY_NOT_FOUND_MESSAGE
    if not lines:
        return NO_MATCHES_MESSAGE

    truncated = len(lines) > effective_max
    rendered: list[str] = []
    for line in lines[:effective_max]:
        rewritten = rewrite_sandbox_grep_line(line)
        if rewritten:
            rendered.append(rewritten)
    if not rendered:
        return NO_MATCHES_MESSAGE

    output_text = "\n".join(rendered)
    if truncated:
        output_text += f"\n...[truncated at {effective_max} results]"
    return output_text


def rewrite_sandbox_grep_line(line: str) -> str | None:
    head, sep, payload = line.partition(":")
    if not sep or ":" not in payload:
        return None
    line_no, _, rest = payload.partition(":")
    if not line_no.isdigit():
        return None
    relative = normalize_posix_path(head)
    if relative.startswith("./"):
        relative = relative[2:]
    if not relative:
        return None
    if len(rest) > MAX_GREP_LINE_CHARS:
        rest = rest[:MAX_GREP_LINE_CHARS] + "..."
    return f"{relative}:{line_no}: {rest.lstrip()}"


__all__ = [
    "DIRECTORY_NOT_FOUND_MESSAGE",
    "NO_MATCHES_MESSAGE",
    "build_sandbox_glob_command",
    "build_sandbox_grep_command",
    "clamp_grep_results",
    "render_sandbox_glob_output",
    "render_sandbox_grep_output",
    "rewrite_sandbox_grep_line",
]
