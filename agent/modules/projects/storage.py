"""Immutable project objects and manifests, with a provider-independent contract."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import uuid4


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_bytes(manifest: dict) -> bytes:
    validate_manifest(manifest)
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def validate_key(key: str) -> str:
    if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
        raise ValueError("Invalid storage checksum.")
    return key


def file_chunks(entry: dict) -> list[dict]:
    return entry.get("chunks", [{"checksum": entry["checksum"], "size": entry["size"]}])


def validate_manifest(manifest: dict) -> None:
    if manifest.get("version") != 1 or not isinstance(manifest.get("entries"), dict):
        raise ValueError("Unsupported project manifest.")
    entries = manifest["entries"]
    for name, entry in entries.items():
        path = PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name or "\x00" in name:
            raise ValueError("Unsafe project manifest path.")
        if str(path) != name or entry.get("type") not in {"file", "directory", "symlink"}:
            raise ValueError("Invalid project manifest entry.")
        if entry["type"] == "file":
            validate_key(entry["checksum"])
            if not isinstance(entry.get("size"), int) or entry["size"] < 0:
                raise ValueError("Invalid project file size.")
            chunks = file_chunks(entry)
            if sum(chunk["size"] for chunk in chunks) != entry["size"]:
                raise ValueError("Invalid project file chunks.")
            for chunk in chunks:
                validate_key(chunk["checksum"])
                if chunk["size"] < 0 or chunk["size"] > 4 * 1024 * 1024:
                    raise ValueError("Invalid project chunk size.")
        if entry["type"] == "symlink":
            target = entry.get("target", "")
            if not target or PurePosixPath(target).is_absolute() or "\\" in target or ":" in target:
                raise ValueError("External project symlinks cannot be restored.")
            parts = list(path.parent.parts)
            for part in PurePosixPath(target).parts:
                if part == "..":
                    if not parts:
                        raise ValueError("External project symlinks cannot be restored.")
                    parts.pop()
                elif part != ".":
                    parts.append(part)
        for parent in path.parents:
            if str(parent) != "." and entries.get(str(parent), {}).get("type") != "directory":
                raise ValueError("Manifest parent must be a directory.")


class ProjectStorageBackend(Protocol):
    """Keys are logical identifiers; implementations need not expose a filesystem."""

    def has_blob(self, workspace: str, digest: str) -> bool: ...
    def put_blob(self, workspace: str, digest: str, content: bytes) -> None: ...
    def get_blob(self, workspace: str, digest: str) -> bytes: ...
    def put_manifest(self, workspace: str, manifest: dict) -> str: ...
    def get_manifest(self, workspace: str, revision: str) -> dict: ...
    def collect(self, workspace: str, revisions: set[str]) -> None: ...


class LocalProjectStorage:
    def __init__(self, root: Path):
        self.root = root
        self._verified_blobs: dict[tuple[str, str], tuple] = {}
        self._verified_files: dict[tuple[str, str], tuple] = {}

    @staticmethod
    def _signature(path: Path) -> tuple:
        info = path.stat()
        return info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino, info.st_dev

    def _base(self, workspace: str) -> Path:
        if not workspace.isdecimal():
            raise ValueError("Invalid project workspace key.")
        return self.root / workspace

    @staticmethod
    def _atomic(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(".tmp-" + uuid4().hex)
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def _blob(self, workspace: str, digest: str) -> Path:
        return self._base(workspace) / "objects" / validate_key(digest)

    def has_blob(self, workspace: str, digest: str) -> bool:
        path = self._blob(workspace, digest)
        try:
            if self._verified_blobs.get((workspace, digest)) == self._signature(path):
                return True
            self.get_blob(workspace, digest)
            return True
        except (FileNotFoundError, ValueError):
            return False

    def put_blob(self, workspace: str, digest: str, content: bytes) -> None:
        if checksum(content) != validate_key(digest):
            raise ValueError("Project object checksum mismatch.")
        path = self._blob(workspace, digest)
        self._atomic(path, content)
        self._verified_blobs[workspace, digest] = self._signature(path)

    def get_blob(self, workspace: str, digest: str) -> bytes:
        path = self._blob(workspace, digest)
        before = self._signature(path)
        content = path.read_bytes()
        if checksum(content) != digest:
            raise ValueError("Project object checksum mismatch.")
        if self._signature(path) != before:
            raise ValueError("Project object changed during verification.")
        self._verified_blobs[workspace, digest] = before
        return content

    def put_manifest(self, workspace: str, manifest: dict) -> str:
        content = manifest_bytes(manifest)
        for entry in manifest["entries"].values():
            if entry["type"] == "file":
                chunks = file_chunks(entry)
                signature = (entry["size"], tuple((chunk["checksum"], chunk["size"],
                    self._signature(self._blob(workspace, chunk["checksum"]))) for chunk in chunks))
                if self._verified_files.get((workspace, entry["checksum"])) == signature:
                    continue
                if len(chunks) == 1 and chunks[0]["checksum"] == entry["checksum"] and self.has_blob(workspace, entry["checksum"]):
                    if self._blob(workspace, entry["checksum"]).stat().st_size != entry["size"]:
                        raise ValueError("Project object size mismatch.")
                    self._verified_files[workspace, entry["checksum"]] = signature
                    continue
                digest = hashlib.sha256()
                for chunk in chunks:
                    blob = self.get_blob(workspace, chunk["checksum"])
                    if len(blob) != chunk["size"]:
                        raise ValueError("Project object size mismatch.")
                    digest.update(blob)
                if digest.hexdigest() != entry["checksum"]:
                    raise ValueError("Project file checksum mismatch.")
                self._verified_files[workspace, entry["checksum"]] = signature
        revision = checksum(content)
        path = self._base(workspace) / "manifests" / (revision + ".json")
        if not path.is_file() or path.read_bytes() != content:
            self._atomic(path, content)
        return revision

    def get_manifest(self, workspace: str, revision: str) -> dict:
        content = (self._base(workspace) / "manifests" / (validate_key(revision) + ".json")).read_bytes()
        if checksum(content) != revision:
            raise ValueError("Project manifest checksum mismatch.")
        manifest = json.loads(content)
        validate_manifest(manifest)
        return manifest

    def collect(self, workspace: str, revisions: set[str]) -> None:
        """Called under the workspace lock, after the database head has committed."""
        referenced = set()
        for revision in revisions:
            manifest = self.get_manifest(workspace, revision)
            referenced.update(chunk["checksum"] for entry in manifest["entries"].values()
                              if entry["type"] == "file" for chunk in file_chunks(entry))
        base = self._base(workspace)
        for path in (base / "objects").glob("*"):
            if path.name not in referenced and not path.name.startswith(".tmp-"):
                path.unlink()
                self._verified_blobs.pop((workspace, path.name), None)
        for key, signature in tuple(self._verified_files.items()):
            if key[0] == workspace and any(chunk[0] not in referenced for chunk in signature[1]):
                self._verified_files.pop(key, None)
        for path in (base / "manifests").glob("*.json"):
            if path.stem not in revisions:
                path.unlink()
