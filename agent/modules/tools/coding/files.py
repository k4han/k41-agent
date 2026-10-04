"""Bounded reads, safe search, and conditional file mutations."""

from __future__ import annotations

import asyncio
import codecs
import base64
import difflib
import hashlib
import os
import re
import shutil
import stat
import subprocess
import uuid
from pathlib import Path
from threading import RLock
from typing import Any

from agent.modules.tools.coding.contracts import CodingError, InvocationContext, RuntimeResult as ToolResult
from agent.modules.tools.coding.paths import PathPermissions
from agent.modules.tools.coding.storage import MAX_MODEL_BYTES, MAX_MODEL_LINES, ensure_workspace_exclude
from agent.modules.workspaces import IGNORED_DIR_NAMES, MAX_IMAGE_READ_BYTES
from agent.modules.workspaces import compile_glob_pattern, match_glob_path, match_include_pattern
from agent.shared.infrastructure.subprocess_utils import hidden_subprocess_kwargs

MAX_EDIT_BYTES = 10 * 1024 * 1024


def file_version(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(64 * 1024):
            hasher.update(block)
    return hasher.hexdigest()


def read_page(path: Path, offset: int = 1, limit: int = MAX_MODEL_LINES) -> dict[str, Any]:
    if not path.is_file():
        raise CodingError("not_found", f"File does not exist: {path}")
    initial = path.stat()
    version = file_version(path)
    selected: list[str] = []
    used = 0
    line_number = 0
    next_offset = None
    line_truncated = False
    with path.open("r", encoding="utf-8-sig", errors="replace", newline=None) as handle:
        while piece := handle.readline(MAX_MODEL_BYTES):
            line_number += 1
            has_newline = piece.endswith("\n")
            if not has_newline:
                remainder = handle.readline(MAX_MODEL_BYTES)
                continued = bool(remainder)
                while remainder and not remainder.endswith("\n"):
                    remainder = handle.readline(MAX_MODEL_BYTES)
                if continued:
                    piece = piece[:2000] + "... [line truncated]\n"
                    line_truncated = True
            if "\x00" in piece:
                raise CodingError("invalid_input", "Binary files cannot be read as text.")
            if line_number < offset:
                continue
            rendered = f"{line_number}: {piece.rstrip(chr(10))}"
            size = len(rendered.encode("utf-8")) + 1
            if len(selected) >= limit or used + size > MAX_MODEL_BYTES - 512:
                next_offset = line_number
                # A single very long line still needs a consumable preview.
                if not selected:
                    rendered = rendered.encode("utf-8")[:MAX_MODEL_BYTES - 1024].decode("utf-8", errors="ignore") + "... [line truncated]"
                    selected.append(rendered)
                    next_offset = line_number + 1
                    line_truncated = True
                break
            selected.append(rendered)
            used += size
    current = path.stat()
    if (initial.st_mtime_ns, initial.st_size) != (current.st_mtime_ns, current.st_size):
        raise CodingError("stale_content", "File changed while it was being read. Read it again.")
    if not selected and offset > 1:
        raise CodingError("invalid_input", "Offset is beyond the end of the file.")
    return {"path": str(path), "content": "\n".join(selected), "offset": offset,
            "next_offset": next_offset, "version": version, "line_truncated": line_truncated}


def read_byte_page(path: Path, byte_offset: int, limit: int = MAX_MODEL_LINES - 2) -> dict[str, Any]:
    """Read exact UTF-8 text without dropping the remainder of long lines."""
    initial = path.stat()
    if byte_offset > initial.st_size:
        raise CodingError("invalid_input", "Byte offset is beyond the end of the file.")
    version = file_version(path)
    with path.open("rb") as handle:
        handle.seek(byte_offset)
        raw = handle.read(MAX_MODEL_BYTES - 1024)
    if raw and raw[0] & 0xC0 == 0x80:
        raise CodingError("invalid_input", "Use next_byte_offset on a UTF-8 boundary.")
    if b"\x00" in raw:
        raise CodingError("invalid_input", "Binary files cannot be read as text.")
    # Ignore only an incomplete code point at the end, never invalid content.
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        text = decoder.decode(raw, final=byte_offset + len(raw) >= initial.st_size)
    except UnicodeDecodeError as exc:
        raise CodingError("invalid_input", "File is not valid UTF-8 text.") from exc
    text = "".join(text.splitlines(keepends=True)[:min(limit, MAX_MODEL_LINES - 2)])
    consumed = len(text.encode("utf-8"))
    current = path.stat()
    if (initial.st_mtime_ns, initial.st_size) != (current.st_mtime_ns, current.st_size):
        raise CodingError("stale_content", "File changed while it was being read. Read it again.")
    return {"path": str(path), "content": text, "byte_offset": byte_offset,
            "next_byte_offset": byte_offset + consumed if byte_offset + consumed < current.st_size else None,
            "version": version, "line_truncated": False}


class FileService:
    def __init__(self, permissions: PathPermissions, *, on_change=None) -> None:
        self.on_change = on_change
        self.permissions = permissions
        self.locks: dict[str, RLock] = {}

    def lock(self, path: Path) -> RLock:
        return self.locks.setdefault(os.path.normcase(str(path)), RLock())

    def prepare(self, name: str, values: dict[str, Any], context: InvocationContext) -> None:
        value = values.get("file_path", values.get("path", ""))
        self.permissions.resolve_path(context, value, "edit" if name in {"edit", "write"} else "read")

    async def execute(self, name: str, values: dict[str, Any], context: InvocationContext) -> ToolResult:
        return await asyncio.to_thread(self._execute, name, values, context)

    def _execute(self, name: str, values: dict[str, Any], context: InvocationContext) -> ToolResult:
        path = self.permissions.resolve_path(context, values.get("file_path", values.get("path", "")),
                    "edit" if name in {"edit", "write"} else "read", allow_interrupt=False)
        if name == "read":
            with path.open("rb") as handle:
                magic = handle.read(16)
            mime = "image/png" if magic.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if magic.startswith(b"\xff\xd8\xff") else "image/gif" if magic.startswith((b"GIF87a", b"GIF89a")) else "image/webp" if magic.startswith(b"RIFF") and magic[8:12] == b"WEBP" else None
            if mime:
                if path.stat().st_size > MAX_IMAGE_READ_BYTES:
                    raise CodingError("invalid_input", "Image exceeds the 5 MiB ingestion limit.")
                with path.open("rb") as handle:
                    raw = handle.read(MAX_IMAGE_READ_BYTES + 1)
                if len(raw) > MAX_IMAGE_READ_BYTES:
                    raise CodingError("invalid_input", "Image exceeds the 5 MiB ingestion limit.")
                return ToolResult(data={"path": str(path), "mime_type": mime, "version": hashlib.sha256(raw).hexdigest()}, content=[
                    {"type": "text", "text": f"Image {path.name} ({mime})"},
                    {"type": "image", "base64": base64.b64encode(raw).decode("ascii"), "mime_type": mime},
                ])
            limit = min(values.get("limit") or MAX_MODEL_LINES, MAX_MODEL_LINES - 1)
            if values.get("byte_offset") is not None:
                page = read_byte_page(path, values["byte_offset"], limit)
            else:
                page = read_page(path, max(values.get("offset", 1), 1), limit)
            content = page.pop("content")
            cursor_key = "next_byte_offset" if "next_byte_offset" in page else "next_offset"
            content += f"\n[version={page['version']}; {cursor_key}={page[cursor_key]}]"
            if page["line_truncated"]:
                content += "\n[Long line truncated; read with byte_offset=0 to recover exact text.]"
            return ToolResult(data=page, content=content, output_truncated=page["line_truncated"],
                              warnings=["Some physical lines exceed the page budget."] if page["line_truncated"] else [])
        if name == "list_dir":
            entries = sorted(path.iterdir(), key=lambda item: item.name.casefold())
            offset, limit = values.get("offset", 1), values.get("limit", 500)
            selected = entries[offset - 1:offset - 1 + limit]
            return ToolResult(data={"entries": [entry.name for entry in selected],
                                    "next_offset": offset + limit if len(entries) >= offset + limit else None},
                              content="\n".join(entry.name + ("/" if entry.is_dir() else "") for entry in selected) or "(empty directory)")
        if name in {"glob", "grep"}:
            return self.search(name, path, values, context)
        with self.lock(path):
            original = self.read_mutation_source(path) if path.exists() else None
            expected = values.get("expected_version")
            if expected and (original is None or hashlib.sha256(original).hexdigest() != expected):
                raise CodingError("stale_content", "File version changed. Read it again before writing.")
            if name == "edit":
                if original is None:
                    raise CodingError("not_found", f"File does not exist: {path}")
                text, bom, ending = self.decode_source(original)
                before, after = values["old_string"].replace("\r\n", "\n"), values["new_string"].replace("\r\n", "\n")
                if not before or before == after:
                    raise CodingError("invalid_input", "old_string must be non-empty and differ from new_string.")
                occurrences = text.count(before)
                if occurrences == 0:
                    raise CodingError("not_found", "old_string does not match the file.")
                if occurrences > 1 and not values.get("replace_all"):
                    raise CodingError("invalid_input", "Multiple exact matches; provide more context or replace_all=True.")
                updated = text.replace(before, after, -1 if values.get("replace_all") else 1)
                result = self.commit(path, original, self.encode_source(updated, bom, ending), context)
                result.data["replacements"] = occurrences if values.get("replace_all") else 1
                return result
            if original is not None:
                before, bom, ending = self.decode_source(original)
                next_text = values["content"].lstrip("\ufeff").replace("\r\n", "\n")
                updated = before + next_text if values.get("append") else next_text
                encoded = self.encode_source(updated, bom, ending)
            else:
                encoded = values["content"].encode("utf-8")
            return self.commit(path, original, encoded, context)

    @staticmethod
    def read_mutation_source(path: Path) -> bytes:
        if path.stat().st_size > MAX_EDIT_BYTES:
            raise CodingError("invalid_input", "File exceeds the 10 MiB mutation limit.")
        with path.open("rb") as handle:
            raw = handle.read(MAX_EDIT_BYTES + 1)
        if len(raw) > MAX_EDIT_BYTES:
            raise CodingError("invalid_input", "File exceeds the 10 MiB mutation limit.")
        return raw

    @staticmethod
    def decode_source(raw: bytes) -> tuple[str, bool, str]:
        if b"\0" in raw:
            raise CodingError("invalid_input", "Binary files cannot be edited as text.")
        text = raw.decode("utf-8-sig", errors="strict")
        return text.replace("\r\n", "\n"), raw.startswith(b"\xef\xbb\xbf"), "\r\n" if "\r\n" in text else "\n"

    @staticmethod
    def encode_source(text: str, bom: bool, ending: str) -> bytes:
        text = text.replace("\r\n", "\n").replace("\n", ending)
        return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")

    def commit(self, path: Path, expected: bytes | None, content: bytes | None, context: InvocationContext) -> ToolResult:
        if content is not None and len(content) > MAX_EDIT_BYTES:
            raise CodingError("invalid_input", "New content exceeds the 10 MiB mutation limit.")
        current = self.read_mutation_source(path) if path.exists() else None
        if current != expected:
            raise CodingError("stale_content", "File changed before mutation; no overwrite was performed.")
        if content == current:
            raise CodingError("invalid_input", "No changes to apply.")
        workspace = Path(context.workspace).resolve()
        if path.is_relative_to(workspace) and path.relative_to(workspace).parts[0] == ".k41-agent":
            ensure_workspace_exclude(workspace)
        # Re-resolve before committing to detect directory/symlink replacement.
        if path.resolve() != path:
            raise CodingError("stale_content", "Target path changed before mutation.")
        if content is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".tmp-{uuid.uuid4().hex[:16]}")
            try:
                temporary.write_bytes(content)
                if expected is not None:
                    os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
                    if self.read_mutation_source(path) != expected:
                        raise CodingError("stale_content", "File changed before replacement.")
                    os.replace(temporary, path)
                else:
                    # Linking a prepared file creates the target exclusively.
                    try:
                        os.link(temporary, path)
                    except FileExistsError as exc:
                        raise CodingError("stale_content", "A file was created at this path concurrently.") from exc
            finally:
                temporary.unlink(missing_ok=True)
        if self.on_change:
            self.on_change(str(path))
        before = (expected or b"").decode("utf-8-sig").replace("\r\n", "\n")
        after = (content or b"").decode("utf-8-sig").replace("\r\n", "\n")
        diff = "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=str(path), tofile=str(path)))
        added = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
        deleted = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
        version = hashlib.sha256(content).hexdigest() if content is not None else None
        summary = f"Changed {path}: +{added}/-{deleted}; version={version}"
        return ToolResult(data={"path": str(path), "diff": diff, "additions": added, "deletions": deleted, "version": version},
                          content=summary, display_content=f"{summary}\n{diff}")

    def search(self, name: str, base: Path, values: dict[str, Any], context: InvocationContext) -> ToolResult:
        pattern = values["pattern"]
        limit = values.get("max_results", 100) if name == "grep" else values.get("limit", 500)
        flags = re.IGNORECASE if values.get("case_insensitive") else 0
        regex = None
        if name == "grep":
            try:
                regex = re.compile(re.escape(pattern) if values.get("fixed_strings") else pattern, flags)
            except re.error as exc:
                raise CodingError("invalid_input", f"Invalid regular expression: {exc}") from exc
        glob_regex = compile_glob_pattern(pattern) if name == "glob" else None
        results: list[Any] = []
        candidates, engine = self.search_candidates(base, values.get("include_dirs", False))
        if name == "grep" and engine == "ripgrep":
            return self.ripgrep(base, candidates, values, context)
        for candidate in sorted(candidates):
            resolved = candidate.resolve()
            if not resolved.is_relative_to(base if base.is_dir() else base.parent):
                continue
            if resolved.is_relative_to(self.permissions.storage.root.resolve()):
                continue
            self.permissions.assert_allowed(context, "read", str(resolved), allow_interrupt=False)
            rel = os.path.relpath(candidate, context.workspace).replace(os.sep, "/")
            if name == "glob":
                sub = os.path.relpath(candidate, base).replace(os.sep, "/")
                if match_glob_path(glob_regex, rel, sub, candidate.is_dir()):
                    results.append(rel + ("/" if candidate.is_dir() else ""))
            elif candidate.is_file() and match_include_pattern(candidate.name, values.get("include")):
                with resolved.open("r", encoding="utf-8", errors="replace") as handle:
                    line_no = 0
                    while piece := handle.readline(MAX_MODEL_BYTES):
                        line_no += 1
                        continuation = not piece.endswith("\n")
                        while continuation:
                            rest = handle.readline(MAX_MODEL_BYTES)
                            continuation = bool(rest) and not rest.endswith("\n")
                        if "\0" in piece:
                            break
                        if regex.search(piece):
                            results.append({"path": rel, "line": line_no, "text": piece.rstrip("\r\n")[:2000]})
                            if len(results) > limit:
                                break
            if len(results) > limit:
                break
        truncated = len(results) > limit
        results = results[:limit]
        text = "\n".join(results) if name == "glob" else "\n".join(f"{match['path']}:{match['line']}: {match['text']}" for match in results)
        return ToolResult(data={"matches": results, "engine": engine, "limit": limit, "truncated": truncated},
                          content=text or "(no matches)", output_truncated=truncated)

    @staticmethod
    def search_candidates(base: Path, include_dirs: bool = False) -> tuple[list[Path], str]:
        engine = "python"
        # rg only enumerates files; each resolved candidate is checked before
        # opening, so a symlink cannot turn search into an external file read.
        candidates: list[Path] | None = None
        rg = shutil.which("rg")
        if rg and base.is_file():
            candidates, engine = [base], "ripgrep"
        if rg and base.is_dir():
            engine = "ripgrep"
            args = [rg, "--files", "--null", "--hidden", "."]
            args.extend(argument for directory in sorted(IGNORED_DIR_NAMES | {".k41-agent"}) for argument in ("--glob", f"!**/{directory}/**"))
            completed = subprocess.run(args, cwd=base, capture_output=True, timeout=30, **hidden_subprocess_kwargs())
            if completed.returncode not in (0, 1):
                raise CodingError("execution_error", completed.stderr.decode("utf-8", errors="replace"))
            candidates = [base / os.fsdecode(part) for part in completed.stdout.split(b"\0") if part]
        if candidates is None:
            candidates = []
            if base.is_file():
                candidates.append(base)
            else:
                for root, dirs, filenames in os.walk(base, followlinks=False):
                    dirs[:] = sorted(directory for directory in dirs if directory not in IGNORED_DIR_NAMES | {".k41-agent"})
                    if include_dirs:
                        candidates.extend(Path(root) / directory for directory in dirs)
                    candidates.extend(Path(root) / filename for filename in sorted(filenames))
        elif include_dirs:
            directories = {ancestor for candidate in candidates for ancestor in candidate.parents if ancestor != base and base in ancestor.parents}
            candidates.extend(sorted(directories))
        return candidates, engine

    def ripgrep(self, base: Path, candidates: list[Path], values: dict[str, Any], context: InvocationContext) -> ToolResult:
        matches: list[dict[str, Any]] = []
        limit = values.get("max_results", 100)
        files = [path for path in sorted(candidates) if path.is_file()
                 and path.resolve().is_relative_to(base if base.is_dir() else base.parent)
                 and not path.resolve().is_relative_to(self.permissions.storage.root.resolve())
                 and match_include_pattern(path.name, values.get("include"))]
        root = base if base.is_dir() else base.parent
        for start in range(0, len(files), 32):
            for path in files[start:start + 32]:
                self.permissions.assert_allowed(context, "read", str(path.resolve()), allow_interrupt=False)
            args = [shutil.which("rg"), "--line-number", "--with-filename", "--no-heading", "--color", "never",
                    "--max-columns", "2000", "--max-columns-preview", "--max-count", str(limit + 1)]
            if values.get("fixed_strings"):
                args.append("--fixed-strings")
            if values.get("case_insensitive"):
                args.append("--ignore-case")
            args += ["-e", values["pattern"], "--", *(str(path) for path in files[start:start + 32])]
            process = subprocess.Popen(args, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       **hidden_subprocess_kwargs())
            try:
                for raw in process.stdout:
                    decoded = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    parsed = re.match(r"^(.*?):(\d+):(.*)$", decoded)
                    if parsed:
                        path, line, text = parsed.groups()
                        matches.append({"path": os.path.relpath(path, context.workspace).replace(os.sep, "/"),
                                        "line": int(line), "text": text})
                    if len(matches) > limit:
                        process.terminate()
                        break
                code = process.wait(timeout=30)
                if code == 2:
                    raise CodingError("invalid_input", "ripgrep rejected the expression or an input path.")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)
                process.stdout.close()
            if len(matches) > limit:
                break
        truncated = len(matches) > limit
        matches = matches[:limit]
        content = "\n".join(f"{match['path']}:{match['line']}: {match['text']}" for match in matches)
        return ToolResult(data={"matches": matches, "engine": "ripgrep", "limit": limit, "truncated": truncated},
                          content=content or "(no matches)", output_truncated=truncated)
