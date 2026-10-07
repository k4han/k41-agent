"""Standard-library sandbox daemon and file-based RPC client.

This module is shipped with the same operation engine used by local tools.
The application validates inputs and approves exact operation resources;
the worker checks those resources again at the actual filesystem access.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import time
from dataclasses import replace
from pathlib import Path

from agent.modules.tools.coding.contracts import CodingError, InvocationContext, RuntimeError, RuntimeResult
from agent.modules.tools.coding.engine import CodingEngine
from agent.modules.tools.coding.files import FileService
from agent.modules.tools.coding.names import PROCESS_TOOLS
from agent.modules.tools.coding.paths import PathPermissions
from agent.modules.tools.coding.processes import ProcessManager, resolve_shell
from agent.modules.tools.coding.storage import OutputStore, atomic_json, digest
from agent.shared.thread_ids import resolve_thread_id, thread_id_aliases_var

MAX_REQUEST_BYTES = 64 * 1024 * 1024


def permission_identity(action, resource, metadata):
    return json.dumps([action, resource, metadata], sort_keys=True)


class WorkerPermissions(PathPermissions):
    def __init__(self, storage):
        self.storage = storage
        self.requests = {}
        self.authorized = None

    def assert_allowed(self, context, action, resource, *, allow_interrupt=True, **metadata):
        identity = permission_identity(action, resource, metadata)
        if self.authorized is not None and identity not in self.authorized:
            raise CodingError("permission_denied", "The resource changed after application authorization.")
        self.requests[identity] = {"action": action, "resource": resource, "metadata": metadata}


class WorkerEngine(CodingEngine):
    def __init__(self, storage, processes, *, authorized=None):
        self.storage = storage
        self.permissions = WorkerPermissions(storage)
        self.permissions.authorized = authorized
        self.files = FileService(self.permissions)
        self.processes = processes
        self.shell = resolve_shell()


class SandboxWorker:
    def __init__(self, output_root: Path, default_root: str):
        self.storage = OutputStore(output_root, remote_worker=True)
        self.storage.cleanup()
        self.storage.start_cleanup()
        self.processes = ProcessManager(self.storage, register_sessions=False)
        self.default_root = default_root
        self.file_locks = {}
        self.invocation_locks = {}
        self.cancelled_calls = set()
        self.active_calls = {}

    async def dispatch(self, request):
        token = thread_id_aliases_var.set(request.get("thread_aliases", {}))
        try:
            return await self._dispatch_with_identity(request)
        finally:
            thread_id_aliases_var.reset(token)

    async def _dispatch_with_identity(self, request):
        request_id = request.get("request_id")
        if request["operation"] == "cancel_call":
            self.cancelled_calls.add(request_id)
            if task := self.active_calls.get(request_id):
                task.cancel()
            return {"cancelled": True}
        if request["operation"] != "execute" or not request_id:
            return await self._dispatch(request)
        if request_id in self.cancelled_calls:
            raise CodingError("cancelled", "The application cancelled this invocation before execution.")
        self.active_calls[request_id] = asyncio.current_task()
        try:
            return await self._dispatch(request)
        except asyncio.CancelledError as exc:
            raise CodingError("cancelled", "The application cancelled this invocation.") from exc
        finally:
            self.active_calls.pop(request_id, None)

    async def _dispatch(self, request):
        operation = request["operation"]
        if operation == "stop_thread":
            thread_id = resolve_thread_id(request["thread_id"])
            jobs = [job for job in self.processes.jobs.values()
                    if resolve_thread_id(job.thread_id) == thread_id
                    or resolve_thread_id(job.thread_id).startswith(f"{thread_id}:sub:")]
            await asyncio.gather(*(self.processes.stop(job) for job in jobs))
            self.processes.stop_thread_now(thread_id)
            return {"stopped": len(jobs)}
        if operation == "close":
            await self.processes.close()
            return {"closed": True}
        if operation == "clear_scratchpads":
            self.storage.clear_scratchpads(resolve_thread_id(request["thread_id"]))
            return {"cleared": True}
        name, values = request["name"], request["values"]
        context = InvocationContext(**request["context"])
        context = replace(context, workspace=str(Path(context.workspace or self.default_root).resolve()))
        self.storage.owner_dir(context)
        if operation == "retain":
            result = RuntimeResult.from_dict(request["result"])
            return self.storage.bound(result, context).to_dict()
        if operation == "migrate_outputs":
            paths = []
            from agent.modules.tools.coding.storage import output_relative_path
            for reference in request["references"]:
                try:
                    if "legacy_content" in request:
                        from agent.modules.tools.coding.storage import MAX_STORED_BYTES
                        path = self.storage.physical_output_path(context, reference)
                        if not path.exists():
                            raw = request["legacy_content"].encode("utf-8")[:MAX_STORED_BYTES]
                            path.write_bytes(raw.decode("utf-8", errors="ignore").encode("utf-8"))
                    self.storage.output_path(context, reference)
                    paths.append(output_relative_path(context, reference))
                except (CodingError, OSError):
                    continue
            return {"output_paths": paths}
        engine = WorkerEngine(self.storage, self.processes,
                              authorized=set(request.get("authorized", [])) if operation == "execute" else None)
        engine.files.locks = self.file_locks
        if operation == "prepare":
            await engine.prepare(name, values, context)
            return {"permissions": list(engine.permissions.requests.values())}
        if operation != "execute":
            raise CodingError("invalid_input", "Unknown sandbox runtime operation.")
        fingerprint_values = {key: value for key, value in values.items()
                              if not (name == "read" and key == "byte_offset" and value is None)}
        fingerprint = digest(json.dumps([name, fingerprint_values], sort_keys=True, ensure_ascii=True))
        key = f"{context.owner}\0{context.agent_name}\0{context.message_id}\0{context.tool_call_id}"
        lock = self.invocation_locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                if context.tool_call_id and self.storage.journal_path(context).exists():
                    return self.storage.begin(context, fingerprint).to_dict()
                await engine.prepare(name, values, context)
                cached = self.storage.begin(context, fingerprint)
                if cached is not None:
                    return cached.to_dict()
                try:
                    result = await engine.execute(name, values, context)
                except (CodingError, OSError, ValueError) as exc:
                    result = failure(exc)
                content = result.model_content(name)
                if content != result.content:
                    if result.display_content is None and isinstance(result.content, str):
                        result.display_content = result.content
                        result.display_truncated |= result.output_truncated
                    result.content = content
                    result.output_truncated = False
                result = self.storage.bound(result, context, tail=name in PROCESS_TOOLS)
                self.storage.complete(context, fingerprint, result)
                return result.to_dict()
        finally:
            if not lock.locked() and not getattr(lock, "_waiters", None):
                self.invocation_locks.pop(key, None)


def failure(exc):
    code = getattr(exc, "code", "not_found" if isinstance(exc, FileNotFoundError)
                   else "invalid_input" if isinstance(exc, ValueError) else "execution_error")
    return RuntimeResult(status="error", error=RuntimeError(code, str(exc), getattr(exc, "details", {})),
                         content=f"[error] {code}: {exc}")


async def serve(runtime_dir: Path, output_root: Path, default_root: str):
    worker = SandboxWorker(output_root, default_root)
    token = (runtime_dir / "token").read_text()
    last_request = time.monotonic()
    closing = asyncio.Event()

    async def handle(reader, writer):
        nonlocal last_request
        last_request = time.monotonic()
        try:
            header = await reader.readexactly(8)
            size = int.from_bytes(header, "big")
            if size > MAX_REQUEST_BYTES:
                raise CodingError("invalid_input", "Sandbox request is too large.")
            request = json.loads(await reader.readexactly(size))
            if request.pop("token", None) != token:
                raise CodingError("permission_denied", "Invalid sandbox runtime token.")
            result = await worker.dispatch(request)
            response = {"result": result}
            if request["operation"] == "close":
                closing.set()
        except (CodingError, OSError, ValueError, KeyError, asyncio.IncompleteReadError) as exc:
            response = {"error": failure(exc).to_dict()}
        except Exception:
            response = {"error": failure(CodingError("unexpected", "Sandbox execution failed unexpectedly.")).to_dict()}
        try:
            raw = json.dumps(response, ensure_ascii=True).encode()
            writer.write(len(raw).to_bytes(8, "big") + raw)
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            last_request = time.monotonic()

    socket_path = runtime_dir / "worker.sock"
    socket_path.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(handle, path=str(socket_path))
    os.chmod(socket_path, 0o600)
    try:
        async with server:
            while not closing.is_set() and time.monotonic() - last_request < 900:
                try:
                    await asyncio.wait_for(closing.wait(), timeout=10)
                except TimeoutError:
                    pass
    finally:
        await worker.processes.close()
        socket_path.unlink(missing_ok=True)


def rpc(runtime_dir: Path, request_path: Path, response_path: Path):
    request = json.loads(request_path.read_text())
    request["token"] = (runtime_dir / "token").read_text()
    raw = json.dumps(request, ensure_ascii=True).encode()
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("Sandbox request is too large.")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(120)
        connection.connect(str(runtime_dir / "worker.sock"))
        connection.sendall(len(raw).to_bytes(8, "big") + raw)

        def receive(size):
            parts = bytearray()
            while len(parts) < size:
                part = connection.recv(min(size - len(parts), 65536))
                if not part:
                    raise ConnectionError("Sandbox runtime disconnected; the outcome must be inspected.")
                parts.extend(part)
            return parts

        size = int.from_bytes(receive(8), "big")
        response = json.loads(receive(size))
    atomic_json(response_path, response)
    request_path.unlink(missing_ok=True)


if __name__ == "__main__":
    if sys.argv[1] == "serve":
        asyncio.run(serve(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]))
    else:
        rpc(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
