"""Workspace transport for portable skill package operations."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from pathlib import Path
import shlex
import time
import uuid

from agent.modules.skills.package_worker import run_operation
from agent.modules.workspaces import get_workspace_file_io, resolve_workspace_ref

logger = logging.getLogger(__name__)


class WorkspacePackageIO:
    def __init__(self, workspace, *, thread_id: str | None = None, boundary: str | None = None):
        self.ref = resolve_workspace_ref(workspace)
        self.thread_id = thread_id
        self.boundary = boundary

    async def operation(self, op: str, path: str = "", **kwargs) -> dict:
        from agent.shared.config import get_config_service

        config = get_config_service()
        limits = {key: config.get_int(f"skills.{key}", default) for key, default in {
            "max_files": 10_000, "max_bytes": 128 * 1024 * 1024, "max_file_bytes": 64 * 1024 * 1024,
        }.items()}
        request = {"op": op, "path": path, "limits": limits, "boundary": self.boundary, **kwargs}
        started = time.monotonic()
        if self.ref.backend == "local":
            result = await asyncio.to_thread(run_operation, self.ref.locator, request)
        else:
            backend = await get_workspace_file_io(self.ref, thread_id=self.thread_id)
            self.ref = backend.ref
            root = str(self.ref.metadata.get("root") or getattr(backend, "root", ""))
            worker = Path(__file__).with_name("package_worker.py").read_bytes()
            generation = hashlib.sha256(worker).hexdigest()[:16]
            prefix = f".k41-agent/package-runtime/{uuid.uuid4().hex}"
            worker_path = f".k41-agent/package-runtime/worker-{generation}.py"
            await backend.write_bytes(worker_path, worker)
            self.ref = backend.ref
            root = str(self.ref.metadata.get("root") or getattr(backend, "root", ""))
            await backend.write_bytes(prefix + ".request", json.dumps({"workspace": root, "request": request}).encode())
            command = "python3 " + " ".join(shlex.quote(f"{root}/{item}") for item in
                                               (worker_path, prefix + ".request", prefix + ".response"))
            executed = await backend.execute(command, timeout=120, max_output_chars=1024)
            if executed.exit_code not in (0, None):
                raise RuntimeError(f"Skill package transport failed: {executed.output}")
            response = json.loads(await backend.read_bytes(prefix + ".response"))
            self.ref = backend.ref
            cleanup = "rm -f -- " + " ".join(shlex.quote(f"{root}/{prefix}{suffix}") for suffix in (".request", ".response"))
            try:
                await backend.execute(cleanup, timeout=10)
            except Exception:
                logger.debug("Could not clean up skill package transport artifacts", exc_info=True)
            if "error" in response:
                error = response["error"]
                exception = {"FileNotFoundError": FileNotFoundError, "FileExistsError": FileExistsError,
                             "ValueError": ValueError, "BadZipFile": ValueError, "PermissionError": PermissionError}.get(error["type"], RuntimeError)
                raise exception(error["message"])
            result = response["result"]
        logger.info("Skill package operation=%s backend=%s duration_seconds=%.3f cache_reused=%d", op, self.ref.backend,
                    time.monotonic() - started, result.get("reused", 0))
        return result

    async def archive(self, path: str) -> tuple[bytes, str]:
        result = await self.operation("archive", path)
        return base64.b64decode(result["data"]), result["version"]

    async def read(self, path: str) -> tuple[bytes, dict]:
        result = await self.operation("read", path)
        return base64.b64decode(result.pop("data")), result

    async def write(self, path: str, data: bytes, *, expected_version: str | None = None, mode: int = 0o644) -> dict:
        return await self.operation("write", path, data=base64.b64encode(data).decode(), expected_version=expected_version, mode=mode)

    async def install(self, packages: list[tuple[str, bytes]], *, overwrite=False, reuse=False, expected_versions=None, manifest=None) -> dict:
        return await self.operation("install", packages=[{"path": path, "data": base64.b64encode(data).decode()}
                                                        for path, data in packages], overwrite=overwrite, reuse=reuse,
                                    **({"expected_versions": expected_versions} if expected_versions is not None else {}),
                                    **({"manifest": manifest} if manifest is not None else {}))
