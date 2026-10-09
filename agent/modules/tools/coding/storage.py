"""Workspace-shared output retention and thread-scoped invocation records."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from agent.modules.tools.coding.contracts import CodingError, InvocationContext, RuntimeResult as ToolResult
from agent.shared.thread_ids import resolve_thread_id, storage_thread_id
from agent.modules.tools.runtime.output_policy import (
    MAX_CAPTURE_BYTES, MAX_MODEL_BYTES, MAX_MODEL_LINES, MAX_STORED_BYTES, RETENTION_SECONDS, bounded_text,
)


def conversation_key(thread_id: str) -> str:
    return digest(storage_thread_id(thread_id).split(":sub:", 1)[0])[:24]


def output_relative_path(context: InvocationContext, reference: str) -> str:
    return f".k41-agent/outputs/{reference}.txt"


def ensure_workspace_exclude(workspace: Path) -> None:
    """Exclude internal files, including repositories using git worktrees."""
    git_dir = workspace / ".git"
    try:
        if git_dir.is_file():
            value = git_dir.read_text(encoding="utf-8").strip()
            if not value.startswith("gitdir: "):
                return
            git_dir = (workspace / value[8:]).resolve()
            common = git_dir / "commondir"
            if common.is_file():
                git_dir = (git_dir / common.read_text(encoding="utf-8").strip()).resolve()
        if not git_dir.is_dir():
            return
        exclude = git_dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if ".k41-agent/" not in existing.splitlines():
            with exclude.open("a", encoding="utf-8") as handle:
                handle.write(("\n" if existing and not existing.endswith("\n") else "") + ".k41-agent/\n")
    except OSError:
        pass


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sync_directory(path: Path) -> None:
    if os.name != "nt":
        handle = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(handle)
        finally:
            os.close(handle)


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".tmp-{uuid.uuid4().hex[:16]}")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


class OutputStore:
    def __init__(self, root: Path, *, result_loader=ToolResult.from_dict, remote_worker: bool = False) -> None:
        self.result_loader = result_loader
        self.root = root
        self.last_cleanup = 0.0
        self.remote_worker = remote_worker
        self.active_paths: set[Path] = set()
        self.cleanup_task: asyncio.Task | None = None
        root.mkdir(parents=True, exist_ok=True)

    def start_cleanup(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self.cleanup_task is None or self.cleanup_task.done():
            self.cleanup_task = loop.create_task(self._periodic_cleanup())

    async def _periodic_cleanup(self) -> None:
        while True:
            await asyncio.sleep(3600)
            try:
                await asyncio.to_thread(self.cleanup)
            except OSError:
                pass

    async def close(self) -> None:
        if self.cleanup_task is not None:
            self.cleanup_task.cancel()
            await asyncio.gather(self.cleanup_task, return_exceptions=True)
            self.cleanup_task = None

    def owner_dir(self, context: InvocationContext) -> Path:
        directory = self.root / digest(context.owner)[:32]
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.root.resolve()):
            raise CodingError("permission_denied", "Output owner directory is not a trusted directory.")
        owner_record = directory / "owner.json"
        try:
            existing = json.loads(owner_record.read_text(encoding="utf-8")) if owner_record.exists() else {}
        except (OSError, ValueError):
            existing = {}
        if not isinstance(existing, dict) or "backend" not in existing:
            atomic_json(owner_record, {"workspace": context.workspace, "thread_id": context.thread_id,
                                      "backend": context.backend, "locator": context.locator,
                                      "physical": context.backend == "local" or self.remote_worker})
        return directory

    def physical_output_path(self, context: InvocationContext, reference: str, *, create: bool = True) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", reference):
            raise CodingError("invalid_input", "Invalid output reference.")
        if context.backend != "local" and not self.remote_worker:
            raise CodingError("unsupported_capability", "Remote output must be written inside its sandbox.")
        base = Path(context.workspace).resolve()
        path = base / output_relative_path(context, reference)
        if any(parent.is_symlink() for parent in path.parents if parent != base and parent.is_relative_to(base)):
            raise CodingError("permission_denied", "Output storage directories may not be symlinks.")
        if not path.resolve().is_relative_to(base):
            raise CodingError("permission_denied", "Output path escapes the workspace.")
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
            ensure_workspace_exclude(base)
        return path

    def clear_thread_grants(self, thread_id: str) -> None:
        thread_id = resolve_thread_id(thread_id)
        for directory in self.root.iterdir():
            owner = directory / "owner.json"
            if directory.is_symlink() or not owner.is_file():
                continue
            try:
                record = json.loads(owner.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            owner_thread = resolve_thread_id(record.get("thread_id", ""))
            if owner_thread == thread_id or owner_thread.startswith(f"{thread_id}:sub:"):
                for grant in directory.glob("*.grant"):
                    grant.unlink(missing_ok=True)

    def create_output(self, context: InvocationContext) -> tuple[str, Path]:
        if time.time() - self.last_cleanup > 3600:
            self.cleanup()
        reference = uuid.uuid4().hex
        self.owner_dir(context)
        path = self.physical_output_path(context, reference)
        path.touch(exist_ok=False)
        return reference, path

    def output_path(self, context: InvocationContext, reference: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", reference):
            raise CodingError("invalid_input", "Invalid output reference.")
        path = self.physical_output_path(context, reference, create=False)
        if not path.exists():
            candidates = [self.owner_dir(context) / f"{reference}.output"]
            if path.parent.is_dir():
                candidates.extend(directory / f"{reference}.txt" for directory in path.parent.iterdir()
                                  if re.fullmatch(r"[a-f0-9]{24}", directory.name)
                                  and directory.is_dir() and not directory.is_symlink())
            for legacy in candidates:
                if legacy.is_file() and not legacy.is_symlink() and legacy.stat().st_mtime >= time.time() - RETENTION_SECONDS:
                    self.physical_output_path(context, reference)
                    shutil.copy2(legacy, path)
                    break
        if path.is_symlink() or not path.is_file():
            raise CodingError("not_found", "Output reference does not exist in this workspace.")
        return path

    def bound(self, result: ToolResult, context: InvocationContext, *, tail: bool = False) -> ToolResult:
        if result.display_content is not None:
            display = self.bound(ToolResult(content=result.display_content), context, tail=tail)
            result.display_content = display.content
            result.display_truncated |= display.output_truncated
            result.capture_truncated |= display.capture_truncated
            result.output_refs.extend(display.output_refs)
            result.output_paths.extend(display.output_paths)
        # Media blocks survive bounding; their payloads are not rendered as text.
        blocks = result.content if isinstance(result.content, list) else None
        text = result.content if blocks is None else "\n".join(
            block.get("text", "") for block in blocks if block.get("type") == "text"
        )
        _, truncated = bounded_text(text, tail=tail)
        def bound_metadata(value: Any) -> None:
            if isinstance(value, dict):
                if isinstance(value.get("diff"), str):
                    value["diff"], cut = bounded_text(value["diff"], tail=tail)
                    if cut:
                        value["diff_truncated"] = True
                        value["diff_output_refs"] = list(result.output_refs)
                        value["diff_output_paths"] = list(result.output_paths)
                if {"path", "line", "text"}.issubset(value) and isinstance(value["text"], str):
                    marker = "[output truncated]"
                    if result.output_paths:
                        marker = f"[output truncated; read file_path={result.output_paths[0]} byte_offset=0]"
                    value["text"], cut = bounded_text(value["text"], marker, tail=tail)
                    if cut:
                        value["text_truncated"] = True
                for item in value.values():
                    bound_metadata(item)
            elif isinstance(value, list):
                for item in value:
                    bound_metadata(item)
        if not truncated:
            bound_metadata(result.data)
            return result
        path = None
        try:
            reference, path = self.create_output(context)
            raw = text.encode("utf-8")
            path.write_bytes(raw[:MAX_STORED_BYTES].decode("utf-8", errors="ignore").encode("utf-8"))
            result.capture_truncated |= len(raw) > MAX_STORED_BYTES
            result.output_refs.append(reference)
            relative = output_relative_path(context, reference)
            result.output_paths.append(relative)
            marker = f"[output truncated; read file_path={relative}; use byte_offset=0 for long lines]"
            if result.capture_truncated:
                marker += " [capture quota exceeded; some output was lost]"
        except (CodingError, OSError, ValueError) as exc:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            marker = "[output truncated; failed to retain output; some output was lost]"
            result.capture_truncated = True
            result.warnings.append(f"Output retention failed: {exc}")
        preview, _ = bounded_text(text, marker, tail=tail)
        result.content = preview if blocks is None else [
            {"type": "text", "text": preview},
            *(block for block in blocks if block.get("type") != "text"),
        ]
        result.output_truncated = True
        bound_metadata(result.data)
        return result

    def cleanup(self) -> None:
        self.last_cleanup = time.time()
        cutoff = time.time() - RETENTION_SECONDS
        visited_workspaces: set[Path] = set()
        for directory in self.root.iterdir():
            if directory.is_symlink() or not directory.is_dir():
                continue
            for path in directory.glob("*.output"):
                if not path.is_symlink() and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            try:
                record = json.loads((directory / "owner.json").read_text(encoding="utf-8"))
                if not record.get("physical", record.get("backend", "local") == "local"):
                    continue
                base = Path(record["workspace"]).resolve()
                if base in visited_workspaces:
                    continue
                visited_workspaces.add(base)
                output_dir = base / ".k41-agent" / "outputs"
                if not output_dir.resolve().is_relative_to(base) or output_dir.is_symlink():
                    continue
                directories = [output_dir]
                if output_dir.is_dir():
                    directories.extend(directory for directory in output_dir.iterdir()
                                       if re.fullmatch(r"[a-f0-9]{24}", directory.name)
                                       and directory.is_dir() and not directory.is_symlink())
                for retained_dir in directories:
                    for path in retained_dir.glob("*.txt"):
                        if (re.fullmatch(r"[a-f0-9]{32}\.txt", path.name) and not path.is_symlink()
                                and path not in self.active_paths and path.stat().st_mtime < cutoff):
                            path.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError):
                continue

    def journal_path(self, context: InvocationContext) -> Path:
        key = digest(f"{context.agent_name}\0{context.message_id}\0{context.tool_call_id}")
        return self.owner_dir(context) / f"{key[:32]}.invocation"

    def begin(self, context: InvocationContext, fingerprint: str) -> ToolResult | None:
        if not context.tool_call_id:
            return None
        path = self.journal_path(context)
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump({"fingerprint": fingerprint, "state": "started"}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            sync_directory(path.parent)
        except FileExistsError:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:
                raise CodingError("unknown_outcome", "The invocation journal is incomplete; execution will not be retried.") from exc
            if not isinstance(record, dict) or not isinstance(record.get("fingerprint"), str) or record.get("state") not in {"started", "completed"}:
                raise CodingError("unknown_outcome", "The invocation journal is invalid; execution will not be retried.")
            if record["fingerprint"] != fingerprint:
                raise CodingError("invalid_input", "Tool call ID was reused with different arguments.")
            if record["state"] != "completed":
                raise CodingError("unknown_outcome", "This invocation started without a completed result. Inspect its effects; it will not be retried automatically.")
            try:
                return self.result_loader(record["result"])
            except (KeyError, TypeError, ValueError) as exc:
                raise CodingError("unknown_outcome", "The completed journal has no valid result; execution will not be retried.") from exc
        return None

    def complete(self, context: InvocationContext, fingerprint: str, result: ToolResult) -> None:
        if context.tool_call_id:
            atomic_json(self.journal_path(context), {
                "state": "completed", "fingerprint": fingerprint, "result": result.to_dict() if isinstance(result, ToolResult) else result.model_dump(),
            })
