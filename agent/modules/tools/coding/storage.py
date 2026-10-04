"""Owner-scoped output retention and durable invocation settlement records."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from agent.modules.tools.coding.contracts import CodingError, InvocationContext, RuntimeResult as ToolResult

MAX_MODEL_BYTES = 50 * 1024
MAX_MODEL_LINES = 2000
MAX_CAPTURE_BYTES = 1024 * 1024
MAX_STORED_BYTES = 10 * 1024 * 1024
RETENTION_SECONDS = 7 * 24 * 60 * 60


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


def bounded_text(text: str, marker: str = "", *, tail: bool = False) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_MODEL_BYTES and len(text.splitlines()) <= MAX_MODEL_LINES:
        return text, False
    marker = marker or "[output truncated]"
    allowance = MAX_MODEL_BYTES - len(marker.encode("utf-8")) - 4
    lines = text.splitlines(keepends=True)
    if tail:
        head = "".join(lines[:(MAX_MODEL_LINES - 4) // 2]).encode("utf-8")[:allowance // 2]
        end = "".join(lines[-(MAX_MODEL_LINES - 4) // 2:]).encode("utf-8")[-allowance // 2:]
        return f"{head.decode('utf-8', errors='ignore').rstrip(chr(10))}\n\n{marker}\n\n{end.decode('utf-8', errors='ignore').lstrip(chr(10))}", True
    preview = "".join(lines[:MAX_MODEL_LINES - 2]).encode("utf-8")[:allowance]
    return f"{preview.decode('utf-8', errors='ignore').rstrip(chr(10))}\n\n{marker}", True


class OutputStore:
    def __init__(self, root: Path, *, result_loader=ToolResult.from_dict) -> None:
        self.result_loader = result_loader
        self.root = root
        self.last_cleanup = 0.0
        root.mkdir(parents=True, exist_ok=True)

    def owner_dir(self, context: InvocationContext) -> Path:
        directory = self.root / digest(context.owner)[:32]
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.root.resolve()):
            raise CodingError("permission_denied", "Output owner directory is not a trusted directory.")
        owner_record = directory / "owner.json"
        if not owner_record.exists():
            atomic_json(owner_record, {"workspace": context.workspace, "thread_id": context.thread_id})
        return directory

    def clear_thread_grants(self, thread_id: str) -> None:
        for directory in self.root.iterdir():
            owner = directory / "owner.json"
            if directory.is_symlink() or not owner.is_file():
                continue
            try:
                record = json.loads(owner.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            owner_thread = record.get("thread_id", "")
            if owner_thread == thread_id or owner_thread.startswith(f"{thread_id}:sub:"):
                for grant in directory.glob("*.grant"):
                    grant.unlink(missing_ok=True)

    def create_output(self, context: InvocationContext) -> tuple[str, Path]:
        if time.time() - self.last_cleanup > 3600:
            self.cleanup()
        reference = uuid.uuid4().hex
        path = self.owner_dir(context) / f"{reference}.output"
        path.touch(exist_ok=False)
        return reference, path

    def output_path(self, context: InvocationContext, reference: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", reference):
            raise CodingError("invalid_input", "Invalid output reference.")
        path = self.owner_dir(context) / f"{reference}.output"
        if path.is_symlink() or not path.is_file():
            raise CodingError("not_found", "Output reference does not exist in this workspace/thread.")
        return path

    def bound(self, result: ToolResult, context: InvocationContext, *, tail: bool = False) -> ToolResult:
        if result.display_content is not None:
            display = self.bound(ToolResult(content=result.display_content), context, tail=tail)
            result.display_content = display.content
            result.display_truncated |= display.output_truncated
            result.capture_truncated |= display.capture_truncated
            result.output_refs.extend(display.output_refs)
        # Media blocks survive bounding; their payloads are not rendered as text.
        blocks = result.content if isinstance(result.content, list) else None
        text = result.content if blocks is None else "\n".join(
            block.get("text", "") for block in blocks if block.get("type") == "text"
        )
        _, truncated = bounded_text(text, tail=tail)
        def bound_diffs(value: Any) -> None:
            if isinstance(value, dict):
                if isinstance(value.get("diff"), str):
                    value["diff"], cut = bounded_text(value["diff"], tail=tail)
                    if cut:
                        value["diff_truncated"] = True
                        value["diff_output_refs"] = list(result.output_refs)
                for item in value.values():
                    bound_diffs(item)
            elif isinstance(value, list):
                for item in value:
                    bound_diffs(item)
        if not truncated:
            bound_diffs(result.data)
            return result
        reference, path = self.create_output(context)
        raw = text.encode("utf-8")
        path.write_bytes(raw[:MAX_STORED_BYTES].decode("utf-8", errors="ignore").encode("utf-8"))
        result.capture_truncated |= len(raw) > MAX_STORED_BYTES
        result.output_refs.append(reference)
        preview, _ = bounded_text(text, f"[output truncated; read_tool_output output_ref={reference}]", tail=tail)
        result.content = preview if blocks is None else [
            {"type": "text", "text": preview},
            *(block for block in blocks if block.get("type") != "text"),
        ]
        result.output_truncated = True
        bound_diffs(result.data)
        return result

    def cleanup(self) -> None:
        self.last_cleanup = time.time()
        cutoff = time.time() - RETENTION_SECONDS
        for directory in self.root.iterdir():
            if directory.is_symlink() or not directory.is_dir():
                continue
            for path in directory.glob("*.output"):
                if not path.is_symlink() and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)

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
