"""Pinned snapshot storage shared by checkpoints and sandbox recovery."""

import hashlib
import json
from pathlib import Path
import re
import time
import uuid

from agent.modules.skills.package_worker import native_path, package_lock


class SnapshotStore:
    def __init__(self, root: Path):
        self.root = native_path(root)

    def _registry(self):
        path = self.root / ".registry.json"
        return json.loads(path.read_bytes()) if path.is_file() else {}

    def _write_registry(self, registry):
        temporary = self.root / ("." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps(registry), encoding="utf-8")
            temporary.replace(self.root / ".registry.json")
        finally:
            temporary.unlink(missing_ok=True)

    def retain(self, data: bytes, owner: str) -> str:
        version = hashlib.sha256(data).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        with package_lock(self.root):
            destination = self.root / f"{version}.zip"
            if not destination.is_file():
                temporary = self.root / ("." + uuid.uuid4().hex + ".tmp")
                try:
                    temporary.write_bytes(data)
                    temporary.replace(destination)
                finally:
                    temporary.unlink(missing_ok=True)
            registry = self._registry()
            registry[owner] = sorted({*registry.get(owner, []), version})
            self._write_registry(registry)
            self._cleanup(registry)
        return version

    def read(self, version: str) -> bytes:
        if not re.fullmatch(r"[a-f0-9]{64}", version):
            raise ValueError("Invalid snapshot version.")
        data = (self.root / f"{version}.zip").read_bytes()
        if hashlib.sha256(data).hexdigest() != version:
            raise ValueError("Cached skill snapshot checksum mismatch.")
        return data

    def release(self, owner: str):
        if not self.root.is_dir():
            return
        with package_lock(self.root):
            registry = self._registry()
            registry.pop(owner, None)
            self._write_registry(registry)
            self._cleanup(registry)

    def _cleanup(self, registry):
        retained = {version for versions in registry.values() for version in versions}
        for archive in self.root.glob("*.zip"):
            if not archive.is_symlink() and archive.stem not in retained and archive.stat().st_mtime < time.time() - 7 * 86400:
                archive.unlink()
