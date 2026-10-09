"""Portable, standard-library package operations for local and cloud workspaces."""

from __future__ import annotations

import base64
from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
import tempfile
import time
import threading
import uuid
import zipfile

IGNORED = {".git", ".venv", "node_modules", "__pycache__", ".cache", ".pytest_cache", ".k41-agent"}
DEFAULT_LIMITS = {"max_files": 10_000, "max_bytes": 128 * 1024 * 1024, "max_file_bytes": 64 * 1024 * 1024}
_VERSION_CACHE: OrderedDict[str, tuple[str, str]] = OrderedDict()
_VERSION_CACHE_LOCK = threading.Lock()


def _lease_pid_alive(pid: int) -> bool:
    """Check lease PID liveness without delivering signals.

    run_operation executes inside asyncio.to_thread workers, and probing with
    os.kill(pid, 0) on Windows from a worker thread leaves a stray SIGINT
    pending that surfaces as KeyboardInterrupt during event loop shutdown.
    psutil is a hard dependency, so prefer it; keep the os.kill probe for
    POSIX where signal 0 is a pure existence check.
    """
    if os.name == "nt":
        try:
            import psutil
        except ImportError:
            return True
        try:
            return psutil.pid_exists(pid)
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def native_path(path: Path) -> Path:
    resolved = path.resolve()
    if os.name == "nt" and not str(resolved).startswith("\\\\?\\"):
        raw = str(resolved)
        return Path("\\\\?\\UNC\\" + raw[2:] if raw.startswith("\\\\") else "\\\\?\\" + raw)
    return resolved


def safe_path(root: Path, value: str, *, allow_root: bool = False) -> Path:
    raw = str(value).replace("\\", "/")
    if PurePosixPath(raw).is_absolute() or PureWindowsPath(raw).drive or ".." in raw.split("/"):
        raise ValueError("Path escapes the package root.")
    target = (root / raw).resolve()
    if not target.is_relative_to(root) or (target == root and not allow_root):
        raise ValueError("Path escapes the package root.")
    return target


def validate_member(name: str) -> str:
    if "\\" in name or "\x00" in name or any(part in {"", ".", ".."} for part in name.rstrip("/").split("/")):
        raise ValueError("Invalid archive member path.")
    if PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive:
        raise ValueError("Absolute archive member paths are not allowed.")
    for part in name.rstrip("/").split("/"):
        if any(character in part for character in '<>:"|?*') or part.endswith((".", " ")):
            raise ValueError("Archive member is not portable across operating systems.")
        if part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            raise ValueError("Reserved archive member name.")
    return name


def bundled_files(root: Path, limits: dict | None = None) -> list[tuple[str, Path]]:
    root = native_path(root)
    settings = {**DEFAULT_LIMITS, **(limits or {})}
    files: list[tuple[str, Path]] = []
    pending = [(root, "", frozenset())]
    total = 0
    directories = 0
    while pending:
        directory, prefix, ancestors = pending.pop()
        resolved = directory.resolve()
        if not resolved.is_relative_to(root) or resolved in ancestors:
            raise ValueError("Package contains an external or cyclic symbolic link.")
        directories += 1
        if directories > settings["max_files"]:
            raise ValueError("Package directory limit exceeded.")
        for entry in sorted(directory.iterdir()):
            if entry.name in IGNORED or entry.name.startswith(".skill-"):
                continue
            name = f"{prefix}/{entry.name}".lstrip("/")
            validate_member(name)
            target = entry.resolve()
            if not target.is_relative_to(root):
                raise ValueError("Package contains a symbolic link outside its root.")
            if entry.is_dir():
                pending.append((entry, name, ancestors | {resolved}))
            elif entry.is_file():
                size = entry.stat().st_size
                total += size
                if size > settings["max_file_bytes"] or total > settings["max_bytes"] or len(files) >= settings["max_files"]:
                    raise ValueError("Package size or file count limit exceeded.")
                files.append((name, entry))
            else:
                raise ValueError("Special files cannot be included in a skill package.")
    return sorted(files)


def pack_directory(root: Path, limits: dict | None = None) -> bytes:
    root = native_path(root)
    stored_modes = json.loads((root / ".skill-modes.json").read_bytes()) if (root / ".skill-modes.json").is_file() else {}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for current, directories, _ in os.walk(root, followlinks=False):
            directories[:] = [name for name in directories if name not in IGNORED and not name.startswith(".skill-")]
            relative = Path(current).relative_to(root).as_posix()
            if relative != ".":
                validate_member(relative)
                member = zipfile.ZipInfo(relative + "/", (1980, 1, 1, 0, 0, 0))
                member.create_system = 3
                member.external_attr = (stat.S_IFDIR | 0o755) << 16
                archive.writestr(member, b"")
        for name, path in bundled_files(root, limits):
            before = path.stat()
            content = path.read_bytes()
            after = path.stat()
            if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                raise FileExistsError("Package changed while creating its snapshot.")
            member = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            member.create_system = 3
            mode = int(stored_modes.get(name, before.st_mode & 0o777)) if os.name == "nt" else before.st_mode & 0o777
            member.external_attr = (stat.S_IFREG | (mode & 0o777)) << 16
            member.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(member, content)
    return output.getvalue()


def package_version(root: Path, limits: dict | None = None) -> str:
    """Hash the complete portable archive, reusing unchanged local metadata."""
    root = native_path(root)
    fingerprint = hashlib.sha256(json.dumps(limits or {}, sort_keys=True).encode())
    for name, path in bundled_files(root, limits):
        info = path.stat()
        fingerprint.update(json.dumps([name, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode]).encode())
    for current, directories, _ in os.walk(root):
        directories[:] = [name for name in directories if name not in IGNORED and not name.startswith(".skill-")]
        fingerprint.update(Path(current).relative_to(root).as_posix().encode())
    modes = root / ".skill-modes.json"
    if modes.is_file():
        fingerprint.update(modes.read_bytes())
    signature = fingerprint.hexdigest()
    key = str(root)
    with _VERSION_CACHE_LOCK:
        cached = _VERSION_CACHE.get(key)
        if cached and cached[0] == signature:
            _VERSION_CACHE.move_to_end(key)
            return cached[1]
    version = hashlib.sha256(pack_directory(root, limits)).hexdigest()
    with _VERSION_CACHE_LOCK:
        _VERSION_CACHE[key] = (signature, version)
        _VERSION_CACHE.move_to_end(key)
        while len(_VERSION_CACHE) > 128:
            _VERSION_CACHE.popitem(last=False)
    return version


def unpack_archive(content: bytes, destination: Path, limits: dict | None = None) -> None:
    settings = {**DEFAULT_LIMITS, **(limits or {})}
    root = native_path(destination)
    root.mkdir(parents=True, exist_ok=True)
    names: set[str] = set()
    total = 0
    modes = {}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = archive.infolist()
        if len(members) > settings["max_files"]:
            raise ValueError("Archive file count limit exceeded.")
        for member in members:
            if member.flag_bits & 1:
                raise ValueError("Encrypted ZIP members are not supported.")
            name = validate_member(member.filename)
            folded = name.rstrip("/").casefold()
            if folded in names:
                raise ValueError("Duplicate or case-conflicting archive member.")
            names.add(folded)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
                raise ValueError("Archive symbolic links and special files are not allowed.")
            total += member.file_size
            if member.file_size > settings["max_file_bytes"] or total > settings["max_bytes"]:
                raise ValueError("Archive size limit exceeded.")
            target = safe_path(root, name)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("xb") as output:
                remaining = member.file_size
                while block := source.read(min(64 * 1024, remaining + 1)):
                    remaining -= len(block)
                    if remaining < 0:
                        raise ValueError("Archive member exceeds its declared size.")
                    output.write(block)
            os.chmod(target, mode & 0o777 or 0o644)
            modes[name] = mode & 0o777 or 0o644
    (root / ".skill-modes.json").write_text(json.dumps(modes), encoding="utf-8")


def ensure_runtimes(root: Path, requested: list[str]) -> dict:
    """Provision runtime executables in the sandbox-owned cache only."""
    import platform
    import tarfile
    import urllib.request

    binary_root = root / ".k41-agent" / "skill-tools" / "bin"
    binary_root.mkdir(parents=True, exist_ok=True)
    tools = {name: shutil.which(name) or (str(binary_root / name) if (binary_root / name).is_file() else None)
             for name in ("uv", "node", "pnpm", "bash", "pwsh")}
    errors = []
    if os.name == "nt":
        return {"tools": tools, "errors": [f"Missing local runtime: {name}. Install it before running this skill." for name in requested if not tools.get(name)]}
    architecture = {"x86_64": ("x86_64", "x64"), "aarch64": ("aarch64", "arm64")}.get(platform.machine())
    if architecture is None:
        return {"tools": tools, "errors": ["Automatic runtime setup does not support this CPU architecture."]}

    def download(url):
        request = urllib.request.Request(url, headers={"User-Agent": "k41-agent-skills"})
        with urllib.request.urlopen(request, timeout=45) as response:
            content = response.read(128 * 1024 * 1024 + 1)
        if len(content) > 128 * 1024 * 1024:
            raise ValueError("Runtime download exceeds the limit.")
        return content

    with package_lock(root):
        for name in requested:
            if tools.get(name):
                continue
            try:
                if name in {"uv", "pnpm"}:
                    repository = "astral-sh/uv" if name == "uv" else "pnpm/pnpm"
                    release = json.loads(download(f"https://api.github.com/repos/{repository}/releases/latest"))
                    asset_name = f"uv-{architecture[0]}-unknown-linux-gnu.tar.gz" if name == "uv" else f"pnpm-linux-{architecture[1]}"
                    asset = next(item for item in release["assets"] if item["name"] == asset_name)
                    payload = download(asset["browser_download_url"])
                    checksum = asset.get("digest")
                    if checksum and checksum != "sha256:" + hashlib.sha256(payload).hexdigest():
                        raise ValueError("Runtime download checksum mismatch.")
                    if name == "uv":
                        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                            member = next(item for item in archive.getmembers() if item.isfile() and item.name.endswith("/uv"))
                            payload = archive.extractfile(member).read()
                elif name == "node":
                    checksums = download("https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt").decode()
                    checksum, filename = next(line.split() for line in checksums.splitlines() if line.endswith(f"-linux-{architecture[1]}.tar.gz"))
                    payload = download(f"https://nodejs.org/dist/latest-v22.x/{filename}")
                    if hashlib.sha256(payload).hexdigest() != checksum:
                        raise ValueError("Node.js download checksum mismatch.")
                    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                        member = next(item for item in archive.getmembers() if item.isfile() and item.name.endswith("/bin/node"))
                        payload = archive.extractfile(member).read()
                else:
                    raise ValueError(f"Runtime {name} must be installed in the sandbox image.")
                temporary = binary_root / (".skill-tool-" + uuid.uuid4().hex)
                temporary.write_bytes(payload)
                os.chmod(temporary, 0o755)
                temporary.replace(binary_root / name)
                tools[name] = str(binary_root / name)
            except Exception as exc:
                errors.append(f"Runtime {name} could not be prepared: {exc}")
    return {"tools": tools, "errors": errors, "bin_directory": str(binary_root)}


@contextmanager
def package_lock(root: Path):
    lock_root = Path(tempfile.gettempdir()) / "k41-skill-locks"
    lock_root.mkdir(exist_ok=True)
    name = hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()
    with (lock_root / name).open("a+b") as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def run_operation(workspace: str, request: dict) -> dict:
    root = native_path(Path(workspace))
    operation = request["op"]
    value = request.get("path", "")
    target = safe_path(root, value, allow_root=operation in {"scan", "tree", "archive", "versions", "runtime_status", "ensure_runtime", "cleanup", "install"})
    limits = request.get("limits", {})
    if request.get("boundary") is not None and operation not in {"install", "scan", "runtime_status"}:
        boundary = safe_path(root, request["boundary"], allow_root=True)
        if not target.is_relative_to(boundary):
            raise ValueError("Path escapes the selected skill package.")
    if operation == "scan":
        if not target.is_dir():
            return {"skills": []}
        skills = []
        pending = [(target, 0)]
        visited = 0
        while pending:
            directory, depth = pending.pop()
            visited += 1
            if visited > 2000:
                raise ValueError("Skill discovery directory limit exceeded.")
            for child in sorted(directory.iterdir()):
                if not child.is_dir() or child.name.startswith(".") or child.name in IGNORED:
                    continue
                actual = child.resolve()
                if not actual.is_relative_to(target):
                    continue
                document = child / "SKILL.md"
                if document.is_file() and document.resolve().is_relative_to(actual):
                    if document.stat().st_size > 1024 * 1024:
                        continue
                    errors = []
                    try:
                        content = document.read_text(encoding="utf-8-sig")
                    except UnicodeError:
                        content = ""
                        errors.append("SKILL.md must be valid UTF-8 text.")
                    except OSError as exc:
                        content = ""
                        errors.append(str(exc))
                    try:
                        with package_lock(root):
                            version = package_version(actual, limits)
                    except (ValueError, OSError) as exc:
                        version = hashlib.sha256(content.encode()).hexdigest()
                        errors.append(str(exc))
                    skills.append({"directory": child.relative_to(root).as_posix(), "content": content,
                                   "version": version, "errors": errors})
                elif depth < 5 and not child.is_symlink():
                    pending.append((child, depth + 1))
        return {"skills": skills}
    if operation == "resources":
        return {"resources": [name for name, _ in bundled_files(target, limits) if name != "SKILL.md"]}
    if operation == "tree":
        if not target.is_dir():
            raise FileNotFoundError("Package directory was not found.")
        entries = sorted((item for item in target.iterdir() if item.name not in IGNORED and not item.name.startswith(".skill-")),
                         key=lambda item: item.name.casefold())
        offset = max(0, int(request.get("offset", 0)))
        limit = min(500, max(1, int(request.get("limit", 500))))
        result = []
        for child in entries[offset:offset + limit]:
            if not child.resolve().is_relative_to(boundary if request.get("boundary") is not None else root):
                continue
            info = child.stat()
            result.append({"name": child.name, "path": child.relative_to(root).as_posix(),
                           "kind": "directory" if child.is_dir() else "file", "size": info.st_size,
                           "mode": info.st_mode & 0o777, "mime_type": mimetypes.guess_type(child.name)[0] or "application/octet-stream"})
        return {"entries": result, "next_offset": offset + limit if offset + limit < len(entries) else None}
    if operation == "read":
        if target.stat().st_size > limits.get("max_file_bytes", DEFAULT_LIMITS["max_file_bytes"]):
            raise ValueError("File exceeds the package file limit.")
        content = target.read_bytes()
        return {"data": base64.b64encode(content).decode(), "version": hashlib.sha256(content).hexdigest(),
                "size": len(content), "mime_type": mimetypes.guess_type(target.name)[0] or "application/octet-stream"}
    if operation == "archive":
        with package_lock(root):
            content = pack_directory(target, limits)
        return {"data": base64.b64encode(content).decode(), "version": hashlib.sha256(content).hexdigest()}
    if operation == "versions":
        with package_lock(root):
            versions = {}
            for path in request["paths"]:
                package = safe_path(root, path)
                if request.get("boundary") is not None and not package.is_relative_to(boundary):
                    raise ValueError("Path escapes the selected skill package.")
                versions[path] = package_version(package, limits) if package.is_dir() else None
            return {"versions": versions}
    if operation == "runtime_status":
        return {"tools": {name: shutil.which(name) for name in ("uv", "node", "pnpm", "bash", "pwsh")},
                "shell": os.environ.get("K41_SHELL") or (shutil.which("pwsh") or shutil.which("powershell") or os.environ.get("COMSPEC", "cmd.exe")
                    if os.name == "nt" else shutil.which("bash") or "/bin/sh")}
    if operation == "ensure_runtime":
        return {**ensure_runtimes(root, request.get("requested", [])), "shell": os.environ.get("K41_SHELL") or shutil.which("bash") or "/bin/sh"}
    if operation == "cleanup":
        removed = []
        if target.is_dir():
            for candidate in target.iterdir():
                if request.get("conversation") and candidate.name != request["conversation"]:
                    continue
                if candidate.is_symlink() or not candidate.is_dir():
                    continue
                if request.get("cache_only"):
                    manifest = candidate / ".skill-manifest.json"
                    if not candidate.name.startswith("sk-") or not manifest.is_file():
                        continue
                    try:
                        if json.loads(manifest.read_bytes()).get("layout") != "shared-v1":
                            continue
                    except (ValueError, OSError):
                        continue
                # A lease protects any package a running script could still use.
                leased = False
                for lease in candidate.rglob(".skill-lease-*"):
                    try:
                        lease_pid = int(lease.read_text().strip())
                    except (ValueError, OSError):
                        leased = True
                        continue
                    if _lease_pid_alive(lease_pid):
                        leased = True
                    else:
                        lease.unlink(missing_ok=True)
                if leased:
                    continue
                if request.get("all") or candidate.stat().st_mtime < time.time() - 7 * 86400:
                    shutil.rmtree(candidate)
                    removed.append(candidate.name)
        return {"removed": removed}
    root.mkdir(parents=True, exist_ok=True)
    with package_lock(root):
        if operation == "touch":
            os.utime(target)
            return {}
        if operation in {"write", "delete", "rename"}:
            expected = request.get("expected_version")
            if target.is_file():
                current = hashlib.sha256(target.read_bytes()).hexdigest()
                if expected is None or current != expected:
                    raise FileExistsError("File changed; read its current version before changing it.")
            elif expected:
                raise FileExistsError("File no longer exists.")
        if operation == "write":
            content = base64.b64decode(request["data"], validate=True)
            if len(content) > limits.get("max_file_bytes", DEFAULT_LIMITS["max_file_bytes"]):
                raise ValueError("File size limit exceeded.")
            if request.get("boundary") is not None:
                files = bundled_files(boundary, limits)
                total = sum(path.stat().st_size for _, path in files)
                old_size = target.stat().st_size if target.is_file() else 0
                if total - old_size + len(content) > limits.get("max_bytes", DEFAULT_LIMITS["max_bytes"]) or (
                    not target.exists() and len(files) >= limits.get("max_files", DEFAULT_LIMITS["max_files"])
                ):
                    raise ValueError("Package size or file count limit exceeded.")
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = target.stat().st_mode & 0o777 if target.exists() else int(request.get("mode", 0o644)) & 0o777
            temporary = target.parent / (".skill-write-" + uuid.uuid4().hex)
            try:
                temporary.write_bytes(content)
                os.chmod(temporary, mode)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            return {"version": hashlib.sha256(content).hexdigest()}
        if operation == "mkdir":
            target.mkdir(parents=True, exist_ok=False)
            return {}
        if operation == "delete":
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            return {}
        if operation == "rename":
            destination = safe_path(root, request["destination"])
            if request.get("boundary") is not None and not destination.is_relative_to(boundary):
                raise ValueError("Rename destination escapes the selected skill package.")
            if destination.exists():
                raise FileExistsError("Destination already exists.")
            target.rename(destination)
            if request.get("boundary") is not None:
                modes_path = boundary / ".skill-modes.json"
                if modes_path.is_file():
                    modes = json.loads(modes_path.read_bytes())
                    old = target.relative_to(boundary).as_posix()
                    new = destination.relative_to(boundary).as_posix()
                    modes = {(new + key[len(old):] if key == old or key.startswith(old + "/") else key): value for key, value in modes.items()}
                    modes_path.write_text(json.dumps(modes), encoding="utf-8")
            return {}
        if operation == "rename_package":
            document = safe_path(target, "SKILL.md")
            if hashlib.sha256(document.read_bytes()).hexdigest() != request.get("expected_version"):
                raise FileExistsError("Skill document changed; reload before renaming.")
            destination = safe_path(root, request["destination"])
            if destination.exists():
                raise FileExistsError("Destination skill package already exists.")
            original = document.read_bytes()
            target.rename(destination)
            try:
                temporary = destination / ".skill-rename.tmp"
                temporary.write_bytes(base64.b64decode(request["document"], validate=True))
                os.chmod(temporary, (destination / "SKILL.md").stat().st_mode & 0o777)
                temporary.replace(destination / "SKILL.md")
            except Exception:
                (destination / "SKILL.md").write_bytes(original)
                destination.rename(target)
                raise
            return {}
        if operation == "install":
            packages = request["packages"]
            staging = Path(tempfile.mkdtemp(prefix=".skill-install-", dir=root)).resolve()
            committed = []
            backups = []
            reused = 0
            preserve_backups = False
            try:
                for index, package in enumerate(packages):
                    destination = safe_path(root, package["path"])
                    if "expected_versions" in request:
                        expected = request["expected_versions"].get(package["path"])
                        current = package_version(destination, limits) if destination.is_dir() else None
                        if expected != current:
                            raise FileExistsError("Destination package changed after preview; preview the import again.")
                    if destination.exists() and not request.get("overwrite"):
                        if request.get("reuse"):
                            if request.get("manifest"):
                                metadata = destination / ".skill-manifest.json"
                                if not metadata.is_file() or json.loads(metadata.read_bytes()) != request["manifest"]:
                                    raise FileExistsError("Skill cache is incomplete or has a conflicting version.")
                            reused += 1
                            continue
                        raise FileExistsError("A destination skill package already exists.")
                    unpack_archive(base64.b64decode(package["data"], validate=True), staging / str(index), limits)
                    if request.get("manifest"):
                        (staging / str(index) / ".skill-manifest.json").write_text(json.dumps(request["manifest"]), encoding="utf-8")
                for index, package in enumerate(packages):
                    payload = staging / str(index)
                    if not payload.exists():
                        continue
                    destination = safe_path(root, package["path"])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists():
                        backup = staging / f"backup-{index}"
                        destination.rename(backup)
                        backups.append((backup, destination))
                    payload.rename(destination)
                    committed.append(destination)
                return {"paths": [package["path"] for package in packages], "reused": reused}
            except Exception:
                try:
                    for destination in reversed(committed):
                        if destination.is_relative_to(root):
                            shutil.rmtree(destination)
                    for backup, destination in reversed(backups):
                        backup.rename(destination)
                except OSError as exc:
                    preserve_backups = True
                    raise RuntimeError(f"Import rollback failed; original packages are preserved in {staging}.") from exc
                raise
            finally:
                if not preserve_backups:
                    shutil.rmtree(staging)
    raise ValueError("Unknown package operation.")


def main() -> None:
    import sys
    request_path, response_path = map(Path, sys.argv[1:3])
    request = json.loads(request_path.read_text())
    try:
        result = {"result": run_operation(request["workspace"], request["request"])}
    except Exception as exc:
        result = {"error": {"type": type(exc).__name__, "message": str(exc)}}
    response_path.write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
