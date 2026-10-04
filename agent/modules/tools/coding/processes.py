"""Asynchronous observation of isolated, bounded local subprocess jobs.

Pipe readers use worker threads so Windows selector event loops also work.
Blocking reads and process waits never run on the application's event loop.
"""

from __future__ import annotations

import asyncio
import codecs
import io
import os
import platform
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.modules.tools.coding.contracts import CodingError, InvocationContext, RuntimeResult as ToolResult
from agent.modules.tools.coding.storage import MAX_CAPTURE_BYTES, MAX_MODEL_BYTES, MAX_MODEL_LINES, MAX_STORED_BYTES, OutputStore, bounded_text
if os.name == "nt":
    from agent.modules.tools.coding.windows_job import cancel_pipe_read, spawn_contained
else:
    spawn_contained = subprocess.Popen
from agent.modules.tools.runtime.sandbox import build_safe_env
from agent.shared.infrastructure.subprocess_utils import hidden_subprocess_kwargs


def resolve_shell(configured: str | None = None) -> str:
    override = configured or os.environ.get("K41_SHELL")
    if override:
        return override
    if platform.system() == "Windows":
        return shutil.which("pwsh") or shutil.which("powershell") or os.environ.get("COMSPEC", "cmd.exe")
    return shutil.which("bash") or "/bin/sh"


def kill_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        containment = getattr(process, "coding_job", None)
        if containment:
            try:
                containment.terminate()
                return
            except OSError:
                pass
        if process.poll() is not None:
            return
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           capture_output=True, timeout=5, **hidden_subprocess_kwargs())
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if process.poll() is None:
        process.kill()


@dataclass
class ProcessJob:
    id: str
    owner: str
    thread_id: str
    workspace: str
    process: subprocess.Popen[bytes]
    output_ref: str | None
    path: Path | None
    timeout_seconds: float
    storage: OutputStore
    context: InvocationContext
    registry: Any = None
    session_id: str | None = None
    started: float = field(default_factory=time.monotonic)
    status: str = "running"
    capture_truncated: bool = False
    stored_bytes: int = 0
    received_bytes: int = 0
    head: bytes = b""
    tail: bytes = b""
    storage_failed: bool = False
    buffer: bytes = b""
    lock: threading.Lock = field(default_factory=threading.Lock)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None

    def capture(self, stream: Any, label: str) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while raw := os.read(stream.fileno(), 16 * 1024):
                text = decoder.decode(raw)
                if text:
                    self.append(text, label)
            remaining = decoder.decode(b"", final=True)
            if remaining:
                self.append(remaining, label)
        except (OSError, ValueError):
            pass

    def append(self, text: str, label: str = "stdout") -> None:
        payload = text.encode("utf-8")
        prefix = f"[{label} bytes={len(payload)}] ".encode("utf-8")
        raw = prefix + payload
        with self.lock:
            self.received_bytes += len(raw)
            self.head = (self.head + raw)[:MAX_CAPTURE_BYTES // 2]
            self.tail = (self.tail + raw)[-MAX_CAPTURE_BYTES // 2:]
            limit = MAX_STORED_BYTES if self.path is not None else min(MAX_CAPTURE_BYTES, MAX_STORED_BYTES)
            remaining = limit - self.stored_bytes
            kept = raw[:remaining].decode("utf-8", errors="ignore").encode("utf-8")
            if kept:
                if self.path is None:
                    self.buffer += kept
                    self.stored_bytes += len(kept)
                    if not self.storage_failed and (
                        self.stored_bytes > MAX_MODEL_BYTES - 1024
                        or len(self.buffer.decode("utf-8").splitlines()) > MAX_MODEL_LINES - 4
                    ):
                        self.retain()
                elif not self.storage_failed:
                    try:
                        with self.path.open("ab") as handle:
                            handle.write(kept)
                        self.stored_bytes += len(kept)
                    except OSError:
                        self.storage_failed = True
                        self.capture_truncated = True
            self.capture_truncated |= len(kept) < len(raw)

    def retain(self) -> None:
        """Spill buffered output under the caller's lock only when it needs paging."""
        path = None
        try:
            reference, path = self.storage.create_output(self.context)
            path.write_bytes(self.buffer)
        except (CodingError, OSError, ValueError):
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            self.storage_failed = self.capture_truncated = True
            return
        self.output_ref, self.path = reference, path
        self.buffer = b""
        if not self.finished.is_set():
            self.storage.active_paths.add(path)

    def retained_plain(self) -> str:
        """Legacy consumers receive exact text without injected stream labels."""
        with self.lock, (self.path.open("rb") if self.path is not None else io.BytesIO(self.buffer)) as handle:
            output = []
            while True:
                header = bytearray()
                while len(header) < 80 and not header.endswith(b"] "):
                    char = handle.read(1)
                    if not char:
                        break
                    header.extend(char)
                match = re.fullmatch(rb"\[(?:stdout|stderr) bytes=(\d+)\] ", bytes(header))
                if match is None:
                    break
                output.append(handle.read(int(match.group(1))))
            return b"".join(output).decode("utf-8", errors="replace")


class ProcessManager:
    def __init__(self, storage: OutputStore, *, register_sessions: bool = True) -> None:
        self.register_sessions = register_sessions
        self.remote = None
        self.storage = storage
        self.jobs: dict[str, ProcessJob] = {}
        self.retiring_jobs: dict[str, ProcessJob] = {}

    async def start(self, context: InvocationContext, command: str, cwd: Path,
                    timeout_seconds: float, shell: str) -> ProcessJob:
        name = Path(shell).name.lower()
        if name in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
            command = "[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)\n[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n$OutputEncoding = [Console]::OutputEncoding\n" + command
            command += "\n$k41CommandSucceeded = $?\nif (-not $k41CommandSucceeded) { if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; exit 1 }\nexit 0"
            argv = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
        elif name in {"cmd", "cmd.exe"}:
            argv = [shell, "/D", "/S", "/C", command]
        else:
            argv = [shell, "-c", command]
        script = None
        if len(command) > 8000:
            suffix = ".ps1" if "powershell" in name or "pwsh" in name else ".cmd" if "cmd" in name else ".sh"
            script = self.storage.owner_dir(context) / f"{uuid.uuid4().hex}{suffix}"
            script.write_text(command, encoding="utf-8-sig" if suffix == ".ps1" else "utf-8")
            argv = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)] if suffix == ".ps1" else [shell, "/D", "/C", str(script)] if suffix == ".cmd" else [shell, str(script)]
        environment = build_safe_env(extra_vars={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
        options = hidden_subprocess_kwargs(creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        spawn = asyncio.create_task(asyncio.to_thread(spawn_contained, argv, cwd=str(cwd), stdin=subprocess.PIPE,
                                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
                                                    start_new_session=os.name != "nt", **options))
        try:
            try:
                process = await asyncio.shield(spawn)
            except asyncio.CancelledError:
                process = await spawn
                await asyncio.to_thread(kill_tree, process)
                await asyncio.to_thread(process.wait, timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream:
                        stream.close()
                if containment := getattr(process, "coding_job", None):
                    containment.close()
                raise
        except BaseException:
            if script:
                script.unlink(missing_ok=True)
            raise
        registry = None
        session_id = None
        if self.register_sessions:
            from agent.modules.agent_runtime import current_session_id_var, get_active_session_registry
            registry = get_active_session_registry()
            session_id = current_session_id_var.get()
            if session_id:
                registry.register_pid(session_id, process.pid)
        job = ProcessJob(uuid.uuid4().hex, context.owner, context.thread_id, context.workspace,
                         process, None, None, timeout_seconds, self.storage, context, registry, session_id)
        self.jobs[job.id] = job
        job.task = asyncio.create_task(self._watch(job, script))
        return job

    async def _watch(self, job: ProcessJob, script: Path | None) -> None:
        readers = [threading.Thread(target=job.capture, args=(job.process.stdout, "stdout"), daemon=True),
                   threading.Thread(target=job.capture, args=(job.process.stderr, "stderr"), daemon=True)]
        for reader in readers:
            reader.start()
        try:
            try:
                await asyncio.to_thread(job.process.wait, timeout=job.timeout_seconds)
                if job.status == "running":
                    job.status = "completed"
            except subprocess.TimeoutExpired:
                job.status = "timeout"
                await asyncio.to_thread(kill_tree, job.process)
            except asyncio.CancelledError:
                job.status = "cancelled"
                await asyncio.to_thread(kill_tree, job.process)
                raise
        finally:
            # Descendants may retain pipes after their parent exits. Bound drain.
            for reader in readers:
                await asyncio.to_thread(reader.join, 1.5)
            if any(reader.is_alive() for reader in readers):
                job.capture_truncated = True
            await asyncio.to_thread(kill_tree, job.process)
            await asyncio.to_thread(job.process.wait, timeout=5)
            for reader in readers:
                await asyncio.to_thread(reader.join, 0.5)
            if os.name == "nt":
                for reader in readers:
                    if reader.is_alive():
                        cancel_pipe_read(reader)
                        await asyncio.to_thread(reader.join, 0.5)
            for stream in (job.process.stdin, job.process.stdout, job.process.stderr):
                if stream:
                    try:
                        if any(reader.is_alive() for reader in readers):
                            threading.Thread(target=stream.close, daemon=True).start()
                        else:
                            stream.close()
                    except OSError:
                        pass
            if containment := getattr(job.process, "coding_job", None):
                containment.close()
            if job.session_id:
                job.registry.unregister_pid(job.session_id, job.process.pid)
            if script:
                script.unlink(missing_ok=True)
            with job.lock:
                job.finished.set()
                if job.path is not None:
                    self.storage.active_paths.discard(job.path)
            self.retiring_jobs.pop(job.id, None)

    def get(self, context: InvocationContext, process_id: str) -> ProcessJob:
        job = self.jobs.get(process_id)
        if job is None or job.owner != context.owner:
            raise CodingError("not_found", "Process does not exist in this workspace/thread; jobs are not recovered after restart.")
        return job

    async def observe(self, job: ProcessJob, cursor: int = 0, yield_time_ms: int = 0,
                      *, preview: bool = False) -> ToolResult:
        if not job.finished.is_set() and yield_time_ms:
            try:
                await asyncio.wait_for(job.finished.wait(), yield_time_ms / 1000)
            except TimeoutError:
                pass
        with job.lock:
            if cursor < 0 or cursor > job.stored_bytes:
                raise CodingError("invalid_input", "Output cursor is outside the retained output.")
            size = min(MAX_MODEL_BYTES - 1024, job.stored_bytes - cursor)
            if job.path is None:
                raw = job.buffer[cursor:cursor + size]
            else:
                with job.path.open("rb") as handle:
                    handle.seek(cursor)
                    raw = handle.read(size)
            if raw and (raw[0] & 0xC0) == 0x80:
                raise CodingError("invalid_input", "Use a cursor returned by the previous observation, on a UTF-8 boundary.")
            output = raw.decode("utf-8", errors="ignore")
            output = "".join(output.splitlines(keepends=True)[:MAX_MODEL_LINES - 4])
            consumed = len(output.encode("utf-8"))
            more = cursor + consumed < job.stored_bytes
            tail_preview = ""
            if preview and (more or job.capture_truncated):
                output = output.encode("utf-8")[:MAX_MODEL_BYTES // 2 - 1024].decode("utf-8", errors="ignore")
                output = "".join(output.splitlines(keepends=True)[:(MAX_MODEL_LINES - 8) // 2])
                consumed = len(output.encode("utf-8"))
                end = job.tail[-(MAX_MODEL_BYTES // 2 - 1024):].decode("utf-8", errors="ignore")
                end = "".join(end.splitlines(keepends=True)[-(MAX_MODEL_LINES - 8) // 2:])
                tail_preview = "\n[tail preview; subsequent cursor reads continue through the retained output]\n" + end
                more = cursor + consumed < job.stored_bytes
            data = {"process_id": job.id, "state": job.status, "exit_code": job.process.poll(),
                    "cursor": cursor + consumed, "received_bytes": job.received_bytes,
                    "stored_bytes": job.stored_bytes, "tail_preview": bool(tail_preview),
                    "elapsed_seconds": round(time.monotonic() - job.started, 3)}
            relative = job.path.relative_to(Path(job.workspace)).as_posix() if job.path is not None else None
            output_refs = [job.output_ref] if relative is not None else []
            output_paths = [relative] if relative is not None else []
        footer = f"\n\nProcess {job.id}: {job.status}; exit_code={data['exit_code']}; next_cursor={data['cursor']}."
        data["output_paths"] = output_paths
        marker = f"\n[more output; read_process_output process_id={job.id} cursor={data['cursor']}" if more else ""
        if more:
            marker += f"; read file_path={relative} byte_offset=0]" if relative is not None else "]"
        loss_marker = "\n[capture quota exceeded or pipe drain incomplete; some output was lost]" if job.capture_truncated else ""
        content, cut = bounded_text((output or "(no output)") + tail_preview + footer + marker + loss_marker)
        result = ToolResult(status="running" if not job.finished.is_set() else "success", data=data,
                            content=content, output_refs=output_refs, output_paths=output_paths, capture_truncated=job.capture_truncated,
                            output_truncated=more or cut)
        if job.status in {"timeout", "cancelled"}:
            from agent.modules.tools.coding.contracts import RuntimeError as ResultError
            result.status = "error"
            result.error = ResultError(code=job.status, message=f"Process {job.status}.")
        return result

    async def send(self, job: ProcessJob, text: str) -> None:
        if job.finished.is_set() or job.process.poll() is not None:
            raise CodingError("execution_error", "Process is no longer running.")
        def write() -> None:
            if job.process.stdin:
                job.process.stdin.write(text.encode("utf-8"))
                job.process.stdin.flush()
        try:
            await asyncio.wait_for(asyncio.to_thread(write), timeout=3)
        except TimeoutError as exc:
            await self.stop(job)
            raise CodingError("timeout", "Stdin write timed out; the process was stopped.") from exc
        except asyncio.CancelledError:
            await self.stop(job)
            raise

    async def stop(self, job: ProcessJob) -> None:
        if not job.finished.is_set():
            job.status = "cancelled"
            await asyncio.to_thread(kill_tree, job.process)
            await job.finished.wait()

    def stop_thread_now(self, thread_id: str) -> int:
        self.storage.clear_thread_grants(thread_id)
        jobs = [job for job in self.jobs.values() if job.thread_id == thread_id or job.thread_id.startswith(f"{thread_id}:sub:")]
        for job in jobs:
            self.jobs.pop(job.id, None)
            if not job.finished.is_set():
                self.retiring_jobs[job.id] = job
                job.status = "cancelled"
                threading.Thread(target=kill_tree, args=(job.process,), daemon=True).start()
        return len(jobs) + (self.remote.stop_thread_now(thread_id) if self.remote else 0)

    async def stop_thread(self, thread_id: str) -> None:
        jobs = {**self.jobs, **self.retiring_jobs}
        owned = [job for job in jobs.values()
                 if job.thread_id == thread_id or job.thread_id.startswith(f"{thread_id}:sub:")]
        await asyncio.gather(*(self.stop(job) for job in owned))
        self.stop_thread_now(thread_id)

    async def stop_workspace(self, workspace: str) -> None:
        jobs = [job for job in list(self.jobs.values()) if job.workspace == workspace]
        await asyncio.gather(*(self.stop(job) for job in jobs))
        for job in jobs:
            self.jobs.pop(job.id, None)

    async def close(self) -> None:
        await self.storage.close()
        if self.remote:
            await self.remote.close()
        await asyncio.gather(*(self.stop(job) for job in {**self.jobs, **self.retiring_jobs}.values()))
        self.jobs.clear()
        self.retiring_jobs.clear()
