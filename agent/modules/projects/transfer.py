"""Bounded binary checkpoint transfer using existing backend primitives."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shlex
import tempfile
from uuid import uuid4

from agent.modules.projects import snapshot_worker
from agent.modules.projects.storage import LocalProjectStorage, ProjectStorageBackend, file_chunks, validate_manifest
from agent.modules.workspaces import WorkspaceRef


class WorkspaceSnapshotTransfer:
    def __init__(self):
        self._capture_cache: dict[str, tuple[tuple, dict]] = {}

    def _cached_capture(self, ref: WorkspaceRef, store: ProjectStorageBackend, key: str) -> dict:
        identity = (id(store), ref.backend, ref.locator, ref.metadata.get("root", ""))
        previous_identity, previous = self._capture_cache.get(key, (None, {}))
        if identity != previous_identity:
            return {}
        available = {}
        entries = {}
        for name, entry in previous["entries"].items():
            if entry["type"] != "file":
                continue
            for chunk in file_chunks(entry):
                digest = chunk["checksum"]
                if digest not in available:
                    available[digest] = store.has_blob(key, digest)
            if all(available[chunk["checksum"]] for chunk in file_chunks(entry)):
                entries[name] = entry
        return {"entries": entries, "signatures": previous["signatures"]}

    def _remember_capture(self, ref: WorkspaceRef, store: ProjectStorageBackend, key: str, state: dict):
        self._capture_cache[key] = ((id(store), ref.backend, ref.locator, ref.metadata.get("root", "")), state)

    async def _backend(self, ref: WorkspaceRef):
        from agent.modules.workspaces import create_workspace_runtime_backend as _create_workspace_runtime_backend
        return await _create_workspace_runtime_backend(ref)

    async def _remote(self, backend, operation: str, stage: str, *, cache: dict | None = None):
        worker_path = stage + "/worker.py"
        made = await backend.execute_coding_command("mkdir -p -- " + shlex.quote(stage + "/objects"), timeout=30)
        if made.exit_code not in (0, None):
            raise OSError("Cannot create project transfer directory.")
        if cache:
            await backend.upload_coding_file(json.dumps(cache).encode(), stage + "/cache.json")
        await backend.upload_coding_file(Path(snapshot_worker.__file__).read_bytes(), worker_path)
        command = "python3 " + " ".join(shlex.quote(value) for value in (worker_path, operation, backend.root, stage))
        result = await backend.execute_coding_command(command, timeout=600)
        if result.exit_code not in (0, None):
            raise OSError("Project " + operation + " failed: " + result.output[-2000:])

    async def capture(self, ref: WorkspaceRef, store: ProjectStorageBackend, key: str) -> dict:
        if ref.backend == "local":
            if isinstance(store, LocalProjectStorage) and store.root.resolve().is_relative_to(Path(ref.locator).resolve()):
                raise ValueError("Set workspace.project_storage_root to a directory outside the captured workspace.")
            def run():
                with tempfile.TemporaryDirectory(prefix="k41-project-") as stage:
                    cache = self._cached_capture(ref, store, key)
                    (Path(stage) / "cache.json").write_text(json.dumps(cache), encoding="utf-8")
                    manifest = snapshot_worker.capture(ref.locator, stage)
                    validate_manifest(manifest)
                    for entry in manifest["entries"].values():
                        if entry["type"] == "file":
                            for chunk in file_chunks(entry):
                                digest = chunk["checksum"]
                                if not store.has_blob(key, digest):
                                    store.put_blob(key, digest, (Path(stage) / "objects" / digest).read_bytes())
                    self._remember_capture(ref, store, key, json.loads((Path(stage) / "capture-state.json").read_text(encoding="utf-8")))
                    return manifest
            return await asyncio.to_thread(run)
        backend = await self._backend(ref)
        stage = "/tmp/k41-project-" + uuid4().hex
        try:
            cache = await asyncio.to_thread(self._cached_capture, ref, store, key)
            await self._remote(backend, "capture", stage, cache=cache)
            manifest = json.loads(await backend.download_coding_file(stage + "/manifest.json"))
            validate_manifest(manifest)
            for entry in manifest["entries"].values():
                if entry["type"] == "file":
                    for chunk in file_chunks(entry):
                        digest = chunk["checksum"]
                        if not await asyncio.to_thread(store.has_blob, key, digest):
                            content = await backend.download_coding_file(stage + "/objects/" + digest)
                            await asyncio.to_thread(store.put_blob, key, digest, content)
            self._remember_capture(ref, store, key, json.loads(await backend.download_coding_file(stage + "/capture-state.json")))
            return manifest
        finally:
            try:
                await self._remote(backend, "cleanup", stage)
            except Exception:
                pass

    async def restore(self, ref: WorkspaceRef, store: ProjectStorageBackend, key: str, revision: str):
        manifest = await asyncio.to_thread(store.get_manifest, key, revision)
        validate_manifest(manifest)
        if ref.backend == "local":
            raise ValueError("Registered local directories must not be overwritten during recovery.")
        backend = await self._backend(ref)
        stage = "/tmp/k41-project-" + uuid4().hex
        try:
            made = await backend.execute_coding_command("mkdir -p -- " + shlex.quote(stage + "/objects"), timeout=30)
            if made.exit_code not in (0, None):
                raise OSError("Cannot create project transfer directory.")
            await backend.upload_coding_file(json.dumps(manifest).encode(), stage + "/manifest.json")
            uploaded = set()
            for entry in manifest["entries"].values():
                if entry["type"] == "file":
                    for chunk in file_chunks(entry):
                        digest = chunk["checksum"]
                        if digest not in uploaded:
                            content = await asyncio.to_thread(store.get_blob, key, digest)
                            await backend.upload_coding_file(content, stage + "/objects/" + digest)
                            uploaded.add(digest)
            await self._remote(backend, "restore", stage)
        finally:
            try:
                await self._remote(backend, "cleanup", stage)
            except Exception:
                pass

    def materialize(self, store: ProjectStorageBackend, key: str, revision: str, destination: Path):
        manifest = store.get_manifest(key, revision)
        with tempfile.TemporaryDirectory(prefix="k41-project-") as stage:
            objects = Path(stage) / "objects"
            objects.mkdir()
            for entry in manifest["entries"].values():
                if entry["type"] == "file":
                    for chunk in file_chunks(entry):
                        digest = chunk["checksum"]
                        (objects / digest).write_bytes(store.get_blob(key, digest))
            snapshot_worker.restore(str(destination), stage, manifest)

    async def reconcile(self, ref, store, key, expected, desired):
        """Push approved conflict paths through the normal project mutation boundary."""
        validate_manifest(expected)
        validate_manifest(desired)
        if ref.backend == "local":
            def apply():
                with tempfile.TemporaryDirectory(prefix="k41-local-overlay-") as directory:
                    stage = Path(directory)
                    (stage / "objects").mkdir()
                    (stage / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
                    (stage / "manifest.json").write_text(json.dumps(desired), encoding="utf-8")
                    for name, entry in desired["entries"].items():
                        if entry["type"] == "file" and entry != expected["entries"].get(name):
                            for chunk in file_chunks(entry):
                                (stage / "objects" / chunk["checksum"]).write_bytes(store.get_blob(key, chunk["checksum"]))
                    snapshot_worker.overlay(ref.locator, str(stage))
            task = asyncio.create_task(asyncio.to_thread(apply))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            return
        backend = await self._backend(ref)
        stage = "/tmp/k41-project-" + uuid4().hex
        try:
            result = await backend.execute_coding_command("mkdir -p -- " + shlex.quote(stage + "/objects"), timeout=30)
            if result.exit_code not in (0, None):
                raise OSError("Cannot prepare conflict resolution.")
            await backend.upload_coding_file(json.dumps(expected).encode(), stage + "/expected.json")
            await backend.upload_coding_file(json.dumps(desired).encode(), stage + "/manifest.json")
            uploaded = set()
            for name, entry in desired["entries"].items():
                if entry["type"] != "file" or expected["entries"].get(name) == entry:
                    continue
                for chunk in file_chunks(entry):
                    digest = chunk["checksum"]
                    if digest not in uploaded:
                        await backend.upload_coding_file(await asyncio.to_thread(store.get_blob, key, digest), stage + "/objects/" + digest)
                        uploaded.add(digest)
            await self._remote(backend, "overlay", stage)
        finally:
            try:
                await self._remote(backend, "cleanup", stage)
            except Exception:
                pass

    async def verify(self, ref: WorkspaceRef, store: ProjectStorageBackend, key: str, revision: str):
        manifest = await asyncio.to_thread(store.get_manifest, key, revision)
        validate_manifest(manifest)
        if ref.backend == "local":
            await asyncio.to_thread(snapshot_worker.verify, ref.locator, manifest)
            return
        backend = await self._backend(ref)
        stage = "/tmp/k41-project-" + uuid4().hex
        try:
            made = await backend.execute_coding_command("mkdir -p -- " + shlex.quote(stage + "/objects"), timeout=30)
            if made.exit_code not in (0, None):
                raise OSError("Cannot create project verification directory.")
            await backend.upload_coding_file(json.dumps(manifest).encode(), stage + "/manifest.json")
            await self._remote(backend, "verify", stage)
        finally:
            try:
                await self._remote(backend, "cleanup", stage)
            except Exception:
                pass
