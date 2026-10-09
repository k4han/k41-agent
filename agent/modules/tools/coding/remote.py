"""Application-owned transport for the shared Daytona and Modal runtime."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import shlex
import uuid
import zipfile
from dataclasses import asdict
from pathlib import Path

from agent.modules.tools.coding.models import CodingError, ToolResult
from agent.modules.tools.coding.remote_worker import permission_identity
from agent.shared.thread_ids import resolve_thread_id, thread_storage_aliases

logger = logging.getLogger(__name__)

RUNTIME_MODULES = (
    "tools/coding/contracts.py", "tools/coding/engine.py", "tools/coding/files.py",
    "tools/coding/names.py", "tools/coding/paths.py", "tools/coding/processes.py",
    "tools/coding/storage.py", "tools/coding/remote_worker.py", "tools/runtime/sandbox.py",
    "tools/runtime/shell_guard.py", "tools/runtime/output_policy.py", "workspaces/constants.py", "workspaces/search_utils.py",
    "workspaces/posix_utils.py",
)

BOOTSTRAP = r'''
import hashlib, json, os, pathlib, socket, subprocess, sys, time
archive, namespace, generation, expected, workspace = sys.argv[1:]
if sys.version_info < (3, 11):
    raise RuntimeError("Coding tools require Python 3.11 or newer in the sandbox image.")
runtime = pathlib.Path("/tmp") / ("k41-coding-%s-%s-%s" % (os.getuid(), namespace[:12], generation))
runtime.mkdir(mode=0o700, exist_ok=True)
if runtime.is_symlink() or runtime.stat().st_uid != os.getuid():
    raise RuntimeError("Sandbox runtime directory is not trusted.")
os.chmod(runtime, 0o700)
bundle = runtime / "runtime.zip"
if archive:
    source = pathlib.Path(archive)
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
        raise RuntimeError("Sandbox runtime bundle checksum mismatch.")
    source.replace(bundle)
if not bundle.is_file():
    raise RuntimeError("Sandbox runtime bundle is missing.")
token = runtime / "token"
if not token.exists():
    token.write_text(os.urandom(32).hex())
    os.chmod(token, 0o600)
socket_path = runtime / "worker.sock"
live = False
try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(str(socket_path))
        live = True
except OSError:
    pass
if not live:
    socket_path.unlink(missing_ok=True)
    outputs = pathlib.Path.home() / ".k41-agent" / "coding-runtime" / namespace
    daemon_script = ("import asyncio,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                     "from agent.modules.tools.coding.remote_worker import serve; "
                     "asyncio.run(serve(Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4]))")
    with (runtime / "worker.log").open("ab") as log:
        subprocess.Popen([sys.executable, "-c", daemon_script, str(bundle), str(runtime), str(outputs), workspace],
                         cwd=workspace, stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, start_new_session=True)
    for _ in range(100):
        if socket_path.exists():
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("Sandbox coding runtime did not start. Inspect worker.log.")
print(json.dumps({"runtime_dir": str(runtime), "python": sys.executable, "bundle": str(bundle)}))
'''.strip()


def build_runtime_bundle() -> bytes:
    """Package the portable engine without application imports or dependencies."""
    root = Path(__file__).resolve().parents[4]
    paths = [f"agent/modules/{name}" for name in RUNTIME_MODULES]
    paths.append("agent/shared/infrastructure/subprocess_utils.py")
    paths.append("agent/shared/infrastructure/glob_utils.py")
    paths.append("agent/shared/thread_ids.py")
    packages = {"agent", "agent/modules", "agent/shared", "agent/shared/infrastructure"}
    for name in paths:
        parent = Path(name).parent
        while str(parent) != ".":
            packages.add(parent.as_posix())
            parent = parent.parent
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for package in sorted(packages):
            initializer = ""
            if package == "agent/modules/workspaces":
                initializer = ("from .constants import IGNORED_DIR_NAMES, MAX_IMAGE_READ_BYTES\n"
                               "from .search_utils import compile_glob_pattern, match_glob_path, match_include_pattern\n")
            archive.writestr(f"{package}/__init__.py", initializer)
        for name in paths:
            archive.writestr(name, (root / name).read_bytes())
    return output.getvalue()


class SandboxClient:
    def __init__(self, backend, bundle: bytes, namespace: str, generation: str):
        self.backend = backend
        self.bundle = bundle
        self.namespace = namespace
        self.generation = generation
        self.endpoint = None
        self.lock = asyncio.Lock()

    async def ensure_started(self):
        async with self.lock:
            archive = ""
            if self.endpoint is None:
                archive = f"/tmp/k41-coding-{uuid.uuid4().hex}.zip"
                await self.backend.upload_coding_file(self.bundle, archive)
            args = [archive, self.namespace, self.generation,
                    hashlib.sha256(self.bundle).hexdigest(), self.backend.root]
            command = "python3 -c " + shlex.quote(BOOTSTRAP) + " " + " ".join(map(shlex.quote, args))
            result = await self.backend.execute_coding_command(command, timeout=15)
            if result.exit_code not in (0, None):
                raise CodingError("unsupported_capability", f"Sandbox coding runtime initialization failed: {result.output}")
            try:
                self.endpoint = json.loads(result.output.strip())
            except ValueError as exc:
                raise CodingError("execution_error", "Invalid sandbox runtime initialization response.") from exc

    async def call(self, request):
        endpoint = self.endpoint
        request_id = uuid.uuid4().hex
        request_path = f"{endpoint['runtime_dir']}/{request_id}.request"
        response_path = f"{endpoint['runtime_dir']}/{request_id}.response"
        await self.backend.upload_coding_file(json.dumps(request, ensure_ascii=True).encode(), request_path)
        script = ("import sys; sys.path.insert(0, sys.argv[1]); "
                  "from pathlib import Path; from agent.modules.tools.coding.remote_worker import rpc; "
                  "rpc(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))")
        args = [endpoint["bundle"], endpoint["runtime_dir"], request_path, response_path]
        command = shlex.quote(endpoint["python"]) + " -c " + shlex.quote(script) + " " + " ".join(map(shlex.quote, args))
        result = await self.backend.execute_coding_command(command, timeout=120)
        if result.exit_code not in (0, None):
            raise CodingError("unknown_outcome", "Sandbox runtime disconnected. Inspect side effects before issuing a new call.")
        try:
            raw = await self.backend.download_coding_file(response_path)
            response = json.loads(raw)
        finally:
            # Cleanup must not turn a completed mutation into a reported failure.
            try:
                cleanup = "import pathlib,sys; [pathlib.Path(p).unlink(missing_ok=True) for p in sys.argv[1:]]"
                await self.backend.execute_coding_command(
                    shlex.quote(endpoint["python"]) + " -c " + shlex.quote(cleanup) + " "
                    + shlex.quote(request_path) + " " + shlex.quote(response_path), timeout=10)
            except Exception:
                logger.debug("Sandbox RPC artifact cleanup failed", exc_info=True)
        if "error" in response:
            error = response["error"]["error"]
            raise CodingError(error["code"], error["message"], **error.get("details", {}))
        return response["result"]


class RemoteCodingRuntime:
    def __init__(self, service):
        self.service = service
        self.clients = {}
        self.client_lock = asyncio.Lock()
        self.namespace = hashlib.sha256(str(service.storage.root).encode()).hexdigest()[:32]
        self.generation = uuid.uuid4().hex[:12]
        self.bundle = None
        self.cleanup_tasks = set()
        self.loop = None

    async def client(self, context):
        self.loop = asyncio.get_running_loop()
        key = (context.backend, context.locator, context.workspace)
        async with self.client_lock:
            if key not in self.clients:
                from agent.modules.workspaces import WorkspaceRef, get_workspace_file_io
                ref = WorkspaceRef(backend=context.backend, locator=context.locator,
                                   metadata={"root": context.workspace} if context.workspace else {})
                # Do not transparently recreate a sandbox and retry a mutation.
                backend = await get_workspace_file_io(ref)
                if not callable(getattr(backend, "execute_coding_command", None)):
                    raise CodingError("unsupported_capability", f"Backend {context.backend} does not support coding tools.")
                if self.bundle is None:
                    self.bundle = build_runtime_bundle()
                self.clients[key] = SandboxClient(backend, self.bundle, self.namespace, self.generation)
            return self.clients[key]

    @staticmethod
    def request(operation, name, values, context, *, workspace=None):
        fields = asdict(context)
        fields["permission_rules"] = []
        if workspace is not None:
            fields["workspace"] = workspace
        return {"operation": operation, "name": name, "values": values, "context": fields,
                "thread_aliases": thread_storage_aliases(context.thread_id)}

    async def authorize(self, client, name, values, context, *, allow_interrupt):
        await client.ensure_started()
        prepared = await client.call(self.request("prepare", name, values, context, workspace=client.backend.root))
        approved = []
        for request in prepared["permissions"]:
            action, resource, metadata = request["action"], request["resource"], request["metadata"]
            self.service.permissions.assert_allowed(context, action, resource,
                                                    allow_interrupt=allow_interrupt, **metadata)
            approved.append(permission_identity(action, resource, metadata))
        return approved

    async def prepare(self, name, values, context):
        client = await self.client(context)
        await self.authorize(client, name, values, context, allow_interrupt=True)

    async def execute(self, name, values, context):
        client = await self.client(context)
        approved = await self.authorize(client, name, values, context, allow_interrupt=False)
        request = self.request("execute", name, values, context, workspace=client.backend.root)
        request["authorized"] = approved
        request["request_id"] = uuid.uuid4().hex
        try:
            result = ToolResult.model_validate(await client.call(request))
        except asyncio.CancelledError:
            try:
                await asyncio.shield(client.call({"operation": "cancel_call", "request_id": request["request_id"]}))
            except Exception:
                logger.warning("Failed to cancel sandbox coding invocation", exc_info=True)
            await asyncio.shield(self.stop_thread(context.thread_id))
            raise
        if name in {"edit", "write"}:
            from agent.modules.workspaces import invalidate_workspace_metadata_cache
            invalidate_workspace_metadata_cache(client.backend.ref, root=client.backend.root)
            from agent.modules.skills import invalidate_repository_skills_for_path
            paths = [result.data.get("path"), *(item.get("path") for item in result.data.get("applied", []))]
            for path in paths:
                if path and invalidate_repository_skills_for_path(path):
                    break
        return result

    async def retain(self, result, context):
        client = await self.client(context)
        await client.ensure_started()
        request = self.request("retain", "", {}, context, workspace=client.backend.root)
        request["result"] = result.model_dump()
        return ToolResult.model_validate(await client.call(request))

    async def migrate_outputs(self, references, context, *, legacy_content=None):
        client = await self.client(context)
        await client.ensure_started()
        request = self.request("migrate_outputs", "", {}, context, workspace=client.backend.root)
        request["references"] = references
        if legacy_content is not None:
            request["legacy_content"] = legacy_content
        return (await client.call(request))["output_paths"]

    async def stop_thread(self, thread_id):
        thread_id = resolve_thread_id(thread_id)

        async def stop(client):
            try:
                await client.call({"operation": "stop_thread", "thread_id": thread_id,
                                   "thread_aliases": thread_storage_aliases(thread_id)})
            except Exception:
                logger.warning("Failed to stop sandbox coding jobs for thread %s", thread_id, exc_info=True)
        await asyncio.gather(*(stop(client) for client in self.clients.values() if client.endpoint))

    def stop_thread_now(self, thread_id):
        if not self.clients:
            return 0
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            if self.loop and not self.loop.is_closed():
                asyncio.run_coroutine_threadsafe(self.stop_thread(thread_id), self.loop)
            return len(self.clients)
        task = loop.create_task(self.stop_thread(thread_id))
        self.cleanup_tasks.add(task)
        task.add_done_callback(self.cleanup_tasks.discard)
        return len(self.clients)

    async def close(self):
        if self.cleanup_tasks:
            await asyncio.gather(*self.cleanup_tasks)
        for client in self.clients.values():
            if client.endpoint:
                try:
                    await client.call({"operation": "close"})
                except Exception:
                    logger.warning("Failed to close sandbox coding jobs", exc_info=True)
        self.clients.clear()
