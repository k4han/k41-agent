"""Portable, standard-library-only project capture and restore worker."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile

CHUNK_BYTES = 4 * 1024 * 1024
EXCLUDED = frozenset({"node_modules", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".pnpm-store"})
INTERNAL = frozenset({"scratchpad", "uploads", "generated-images"})


class WorkspaceRootUnavailableError(FileNotFoundError):
    """The capture root is unavailable, rather than an individual project entry."""

    code = "runtime_unavailable"


def _signature(path: Path) -> tuple:
    info = path.lstat()
    return info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino, info.st_dev, str(path)


def _inventory(root: Path) -> dict[str, Path]:
    result = {}

    def walk(base: Path, prefix: str = "", *, git: bool = False):
        for path in sorted(base.iterdir()):
            relative = f"{prefix}/{path.name}" if prefix else path.name
            if relative == ".k41-agent" and path.is_symlink():
                raise ValueError("Project internal storage must not be a symlink.")
            if path.name in EXCLUDED or relative == ".k41-agent":
                if relative == ".k41-agent" and path.is_dir() and not path.is_symlink():
                    result[relative] = path
                    for category in sorted(INTERNAL):
                        child = path / category
                        if child.exists() or child.is_symlink():
                            result[f"{relative}/{category}"] = child
                            if child.is_dir() and not child.is_symlink():
                                walk(child, f"{relative}/{category}")
                continue
            if git and path.name in {"worktrees", "gitdir", "commondir"}:
                continue
            if git and path.name.endswith(".lock"):
                raise OSError("Git metadata is being modified; checkpoint will be retried.")
            if path.name == ".git" and path.is_file() and not path.is_symlink():
                pointer = path.read_text(encoding="utf-8").strip()
                if not pointer.startswith("gitdir: "):
                    raise ValueError("Invalid Git worktree metadata.")
                private = (path.parent / pointer[8:]).resolve()
                common = private
                if (private / "commondir").is_file():
                    common = (private / (private / "commondir").read_text().strip()).resolve()
                if not (private / "HEAD").is_file() or not (common / "objects").is_dir():
                    raise ValueError("Git worktree metadata is unavailable.")
                result[relative] = common
                walk(common, relative, git=True)
                walk(private, relative, git=True)
                continue
            result[relative] = path
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                walk(path, relative, git=git or path.name == ".git")

    walk(root)
    return result


def _safe_link(relative: str, target: str) -> None:
    if not target or PurePosixPath(target).is_absolute() or "\\" in target or ":" in target:
        raise ValueError("External project symlinks cannot be saved: " + relative)
    parts = list(PurePosixPath(relative).parent.parts)
    for part in PurePosixPath(target).parts:
        if part == "..":
            if not parts:
                raise ValueError("External project symlinks cannot be saved: " + relative)
            parts.pop()
        elif part != ".":
            parts.append(part)


def capture(root: str, staging: str) -> dict:
    base, stage = Path(root), Path(staging)
    if base.is_symlink():
        raise ValueError("Project workspace root must not be a symlink: " + str(base))
    if not base.is_dir():
        raise WorkspaceRootUnavailableError(
            "Project workspace directory is missing or is not a directory: " + str(base)
        )
    if stage.resolve().is_relative_to(base.resolve()):
        raise ValueError("Checkpoint staging must be outside the captured workspace.")
    objects = stage / "objects"
    objects.mkdir(parents=True, exist_ok=True)
    cache_path = stage / "cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
    for attempt in range(3):
        try:
            inventory = _inventory(base)
            signatures = {name: _signature(path) for name, path in inventory.items()}
            entries = {}
            for name, path in inventory.items():
                mode = signatures[name][0]
                entry = {"mode": stat.S_IMODE(mode)}
                if stat.S_ISDIR(mode):
                    entry["type"] = "directory"
                elif stat.S_ISLNK(mode):
                    target = os.readlink(path).replace(os.sep, "/")
                    _safe_link(name, target)
                    entry.update(type="symlink", target=target)
                elif stat.S_ISREG(mode):
                    if name.endswith("/objects/info/alternates"):
                        raise ValueError("Git repositories with external object alternates cannot be saved.")
                    if cache.get("signatures", {}).get(name) == list(signatures[name]) and name in cache.get("entries", {}):
                        entries[name] = cache["entries"][name]
                        continue
                    digest, chunks, size = hashlib.sha256(), [], 0
                    with path.open("rb") as handle:
                        sanitized = None
                        if name.endswith(".git/config") or name.endswith(".git/config.worktree"):
                            raw = handle.read()
                            raw = re.sub(rb"https://x-access-token:[^@\s]+@github\.com/", b"https://github.com/", raw)
                            flattened = path.absolute() != (base / name).absolute()
                            section, lines = b"", []
                            for line in raw.splitlines(keepends=True):
                                header = re.match(rb"\s*\[([^\]]+)\]", line)
                                if header:
                                    section = header[1].strip().lower()
                                if flattened and section == b"core" and re.match(rb"\s*worktree\s*=", line, re.I):
                                    continue
                                if flattened and section == b"core" and re.match(rb"\s*bare\s*=", line, re.I):
                                    line = b"\tbare = false\n"
                                lines.append(line)
                            sanitized = b"".join(lines)
                        while True:
                            content = sanitized if sanitized is not None else handle.read(CHUNK_BYTES)
                            if not content:
                                break
                            if len(content) > CHUNK_BYTES:
                                raise ValueError("Git configuration exceeds the checkpoint chunk limit.")
                            digest.update(content)
                            key = hashlib.sha256(content).hexdigest()
                            (objects / key).write_bytes(content)
                            chunks.append({"checksum": key, "size": len(content)})
                            size += len(content)
                            if sanitized is not None:
                                break
                    entry.update(type="file", size=size, checksum=digest.hexdigest(), chunks=chunks)
                else:
                    raise ValueError("Unsupported project entry: " + name)
                entries[name] = entry
            after = _inventory(base)
            if set(after) != set(inventory) or any(_signature(after[name]) != value for name, value in signatures.items()):
                raise OSError("Project files changed while saving.")
            manifest = {"version": 1, "entries": entries}
            (stage / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            (stage / "capture-state.json").write_text(json.dumps({"signatures": signatures, "entries": entries}), encoding="utf-8")
            return manifest
        except (FileNotFoundError, OSError):
            if attempt == 2:
                raise


def restore(root: str, staging: str, manifest: dict | None = None) -> None:
    base, stage = Path(root), Path(staging)
    manifest = manifest or json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
    base.mkdir(parents=True, exist_ok=True)
    for name, entry in sorted(manifest["entries"].items(), key=lambda item: (len(PurePosixPath(item[0]).parts), item[0])):
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
            raise ValueError("Unsafe project restore path.")
        target = base.joinpath(*relative.parts)
        if any(parent.is_symlink() for parent in (target, *target.parents) if parent == base or base in parent.parents):
            raise ValueError("Project restore cannot overwrite symlinks.")
        if entry["type"] == "directory":
            target.mkdir(parents=True, exist_ok=True)
        elif entry["type"] == "symlink":
            continue
        elif entry["type"] == "file":
            target.parent.mkdir(parents=True, exist_ok=True)
            digest, size = hashlib.sha256(), 0
            with target.open("xb") as handle:
                for chunk in entry.get("chunks", [{"checksum": entry["checksum"], "size": entry["size"]}]):
                    key = chunk["checksum"]
                    if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
                        raise ValueError("Invalid project object key.")
                    content = (stage / "objects" / key).read_bytes()
                    if len(content) != chunk["size"] or hashlib.sha256(content).hexdigest() != key:
                        raise ValueError("Project restore object is corrupt.")
                    digest.update(content)
                    size += len(content)
                    handle.write(content)
            if size != entry["size"] or digest.hexdigest() != entry["checksum"]:
                raise ValueError("Project restore file is corrupt.")
            target.chmod(entry["mode"] & 0o777)
        else:
            raise ValueError("Unsupported project restore entry.")
    for name, entry in manifest["entries"].items():
        if entry["type"] == "symlink":
            _safe_link(name, entry["target"])
            (base / name).symlink_to(entry["target"])
    for name, entry in manifest["entries"].items():
        if entry["type"] == "directory":
            (base / name).chmod(entry["mode"] & 0o777)


def main():
    operation, root, staging = sys.argv[1:]
    stage = Path(staging)
    if not stage.name.startswith("k41-project-") or stage.parent.resolve() != Path(tempfile.gettempdir()).resolve():
        raise ValueError("Invalid project transfer staging directory.")
    if operation == "capture":
        capture(root, staging)
    elif operation == "restore":
        restore(root, staging)
    elif operation == "cleanup":
        shutil.rmtree(stage, ignore_errors=True)
    else:
        raise ValueError("Unknown project transfer operation.")


if __name__ == "__main__":
    main()
