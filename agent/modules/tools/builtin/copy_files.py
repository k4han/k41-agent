"""Copy files and directories between the main workspace and auxiliary machines.

Auxiliary machines hold temporary data. Copying results into the main workspace
goes through the workspace file and command services, so project and thread
persistence capture the change like any other edit.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import posixpath
import re
import shlex
import tarfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, StructuredTool
from langgraph.prebuilt import ToolRuntime
from pydantic import Field

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import AUXILIARY_MACHINES_TAG, ToolCapability, ToolCategory
from agent.modules.tools.result import ToolError, ToolErrorCode
from agent.modules.tools.runtime.targets import ExecutionTarget, resolve_target

Runtime = Annotated[ToolRuntime[Any, Any], InjectedToolArg]
MAX_COPY_BYTES = 64 * 1024 * 1024
MAX_COPY_FILES = 5000
EXCLUDED_DIRECTORY = ".k41-agent"
TRANSFER_TIMEOUT_SECONDS = 300
REMOTE_PATH_SCRIPT = """
import json, os, sys
root = os.path.realpath(sys.argv[1])
paths = [os.path.realpath(os.path.join(root, path)) for path in json.loads(sys.argv[2])]
if any(os.path.commonpath([root, path]) != root for path in paths):
    print(json.dumps({"error": "Relative paths and symlinks may not escape the workspace."}))
else:
    print(json.dumps({"paths": paths}))
""".strip()


@dataclass
class _Side:
    target: ExecutionTarget
    path: str
    runtime: Any

    @property
    def absolute(self) -> str:
        if self.target.backend == "local":
            from agent.modules.tools.runtime.path_guard import resolve_safe_path

            try:
                return resolve_safe_path(self.target.root, self.path)
            except ValueError as exc:
                raise ToolError(ToolErrorCode.PERMISSION_DENIED, str(exc)) from None
        return posixpath.normpath(posixpath.join(self.target.root or "/", self.path))

    @property
    def label(self) -> str:
        return f"{self.target.locator if self.target.auxiliary else 'workspace'}:{self.path}"

    async def file_io(self):
        """Main workspace: the persistence-aware service. Auxiliary: the machine itself."""
        from agent.modules.tools.runtime.context import get_thread_id
        from agent.modules.workspaces import get_workspace_file_io

        if self.target.auxiliary:
            return await self.raw()
        from agent.modules.tools.builtin.workspace import get_workspace

        return await get_workspace_file_io(get_workspace(self.runtime), thread_id=get_thread_id(self.runtime.config))

    async def raw(self):
        from agent.modules.workspaces import WorkspaceRef, create_workspace_runtime_backend

        if self.target.auxiliary:
            ref = WorkspaceRef(backend=self.target.backend, locator=self.target.locator,
                               metadata={"root": self.target.root})
        else:
            # Project bindings may run the main workspace elsewhere than the thread record says.
            ref = (await self.file_io()).ref
        return await create_workspace_runtime_backend(ref)

    @property
    def remote_root(self) -> str:
        return self.target.root or "/"


def _relative(path: str, name: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    if not value:
        raise ToolError(ToolErrorCode.INVALID_INPUT, f"{name} is required; use '.' for the root.")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ToolError(ToolErrorCode.INVALID_INPUT, f"{name} must be relative to the machine's root.")
    value = posixpath.normpath(value)
    if value == ".." or value.startswith("../"):
        raise ToolError(ToolErrorCode.INVALID_INPUT, f"{name} must stay inside the machine's root.")
    return value


def _too_large(size: int) -> ToolError:
    return ToolError(ToolErrorCode.INVALID_INPUT,
                     f"The copy is {size} bytes; the limit is {MAX_COPY_BYTES}. Copy a narrower path or "
                     "compress large outputs on the machine first.")


async def _remote(backend, command: str) -> str:
    result = await backend.execute_coding_command(command, timeout=TRANSFER_TIMEOUT_SECONDS)
    if result.exit_code not in (0, None):
        raise ToolError(ToolErrorCode.EXECUTION_ERROR, f"Copy command failed: {result.output.strip()[-500:]}")
    return result.output


async def _resolved_paths(side: _Side, paths: list[str]) -> list[str]:
    if side.target.backend == "local":
        return [replace(side, path=path).absolute for path in paths]
    backend = await side.raw()
    resolved: list[str] = []
    # Keep command arguments and transport output bounded for wide directories.
    for start in range(0, len(paths), 50):
        arguments = [backend.root, json.dumps(paths[start:start + 50])]
        command = "python3 -c " + shlex.quote(REMOTE_PATH_SCRIPT) + " " + " ".join(map(shlex.quote, arguments))
        result = json.loads(await _remote(backend, command))
        if result.get("error"):
            raise ToolError(ToolErrorCode.PERMISSION_DENIED, result["error"])
        resolved.extend(result["paths"])
    return resolved


# Reading ------------------------------------------------------------------ #


async def _read(side: _Side) -> tuple[str, bytes]:
    """Return ("file", content) or ("directory", gzipped tar of its contents)."""
    if side.target.backend == "local":
        return await asyncio.to_thread(_read_local, Path(side.absolute),
                                       authorize=lambda path: _authorize_path(side, "read", str(path)))
    backend = await side.raw()
    [absolute] = await _resolved_paths(side, [side.path])
    _authorize_path(side, "read", absolute)
    path = shlex.quote(absolute)
    kind = (await _remote(backend, f"if [ -d {path} ]; then echo directory; elif [ -f {path} ]; "
                                   f"then wc -c < {path}; else echo missing; fi")).strip().splitlines()[-1:]
    kind = kind[0].strip() if kind else "missing"
    if kind == "missing":
        raise ToolError(ToolErrorCode.NOT_FOUND, f"{side.label} does not exist.")
    if kind != "directory":
        if int(kind) > MAX_COPY_BYTES:
            raise _too_large(int(kind))
        return "file", await backend.download_coding_file(absolute)
    archive = f"/tmp/k41-copy-{uuid.uuid4().hex}.tgz"
    try:
        size = int((await _remote(backend, f"tar -czf {archive} --exclude=./{EXCLUDED_DIRECTORY} -C {path} . "
                                           f"&& wc -c < {archive}")).strip().splitlines()[-1])
        if size > MAX_COPY_BYTES:
            raise _too_large(size)
        return "directory", await backend.download_coding_file(archive)
    finally:
        await _remote(backend, f"rm -f {archive}")


def _read_local(path: Path, *, authorize=None) -> tuple[str, bytes]:
    if path.is_file():
        if authorize is not None:
            authorize(path)
        size = path.stat().st_size
        if size > MAX_COPY_BYTES:
            raise _too_large(size)
        return "file", path.read_bytes()
    if not path.is_dir():
        raise ToolError(ToolErrorCode.NOT_FOUND, f"{path} does not exist.")
    total = 0
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for current, directories, files in os.walk(path):
            directories[:] = [name for name in directories if name != EXCLUDED_DIRECTORY
                              and not (Path(current) / name).is_symlink()
                              and not (Path(current) / name).is_junction()]
            for name in files:
                full = Path(current) / name
                if full.is_symlink():
                    continue
                if authorize is not None:
                    authorize(full)
                total += full.stat().st_size
                if total > MAX_COPY_BYTES:
                    raise _too_large(total)
                archive.add(full, arcname=full.relative_to(path).as_posix(), recursive=False)
    return "directory", output.getvalue()


# Writing ------------------------------------------------------------------ #


def _members(data: bytes) -> list[tuple[str, bytes]]:
    """Regular files of an archive with safe relative names."""
    files: list[tuple[str, bytes]] = []
    names: set[str] = set()
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            name = posixpath.normpath(member.name.replace("\\", "/"))
            if name.startswith(("/", "../")) or name in {"..", "."} or re.match(r"^[A-Za-z]:", name):
                raise ToolError(ToolErrorCode.INVALID_INPUT, f"Archive entry escapes the destination: {member.name}")
            if EXCLUDED_DIRECTORY in name.split("/"):
                continue
            if name in names:
                raise ToolError(ToolErrorCode.INVALID_INPUT, f"Duplicate archive entry: {member.name}")
            names.add(name)
            total += member.size
            if total > MAX_COPY_BYTES:
                raise _too_large(total)
            handle = archive.extractfile(member)
            files.append((name, handle.read() if handle else b""))
            if len(files) > MAX_COPY_FILES:
                raise ToolError(ToolErrorCode.INVALID_INPUT,
                                f"The directory has more than {MAX_COPY_FILES} files. Copy a narrower path.")
    return files


def _regular_archive(data: bytes, members: list[tuple[str, bytes]]) -> bytes:
    """Only extract validated regular files; retain their modes but never links."""
    output = io.BytesIO()
    contents = dict(members)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as source:
        with tarfile.open(fileobj=output, mode="w:gz") as destination:
            for member in source:
                name = posixpath.normpath(member.name.replace("\\", "/"))
                if member.isfile() and name in contents:
                    member.name = name
                    member.pax_headers = {}
                    destination.addfile(member, io.BytesIO(contents[name]))
    return output.getvalue()


async def _write(side: _Side, kind: str, data: bytes, *, members: list[tuple[str, bytes]] | None = None) -> int:
    """Write the payload and return the number of files written."""
    if kind == "file":
        await (await side.file_io()).write_bytes(side.path, data)
        return 1
    if members is None:
        members = await asyncio.to_thread(_members, data)
    if side.target.backend == "local":
        # Per-file writes keep project persistence aware of every change.
        file_io = await side.file_io()
        for name, content in members:
            await file_io.write_bytes(posixpath.join(side.path, name) if side.path != "." else name, content)
        return len(members)
    count = len(members)
    data = await asyncio.to_thread(_regular_archive, data, members)
    raw = await side.raw()
    archive = f"/tmp/k41-copy-{uuid.uuid4().hex}.tgz"
    await raw.upload_coding_file(data, archive)
    [absolute] = await _resolved_paths(side, [side.path])
    destination = shlex.quote(absolute)
    command = f"mkdir -p {destination} && tar -xzf {archive} --no-same-owner -C {destination}; status=$?; rm -f {archive}; exit $status"
    if side.target.auxiliary:
        await _remote(raw, command)
    else:
        # The main workspace goes through the command service so its changes are captured.
        result = await (await side.file_io()).execute(command, timeout=TRANSFER_TIMEOUT_SECONDS)
        if result.exit_code not in (0, None):
            raise ToolError(ToolErrorCode.EXECUTION_ERROR, f"Copy command failed: {result.output.strip()[-500:]}")
    return count


# Tool --------------------------------------------------------------------- #


def _authorize(runtime, source: _Side, destination: _Side) -> None:
    from agent.modules.tools.coding.adapter import invocation_context
    from agent.modules.tools.coding.models import CodingError
    from agent.modules.tools.coding.service import get_coding_service

    context = invocation_context(runtime)
    permissions = get_coding_service().permissions
    try:
        # Path rules for the main workspace still apply to copies.
        if not source.target.auxiliary:
            permissions.assert_allowed(context, "read", source.absolute)
        if not destination.target.auxiliary:
            permissions.assert_allowed(context, "edit", destination.absolute)
        permissions.assert_allowed(context, "copy_files", f"{source.label} -> {destination.label}")
    except CodingError as exc:
        raise ToolError(ToolErrorCode.PERMISSION_DENIED, str(exc)) from None


def _authorize_path(side: _Side, action: str, absolute: str) -> None:
    if side.target.auxiliary:
        return
    from agent.modules.tools.coding.adapter import invocation_context
    from agent.modules.tools.coding.models import CodingError
    from agent.modules.tools.coding.service import get_coding_service

    context = invocation_context(side.runtime)
    permissions = get_coding_service().permissions
    try:
        if side.target.backend == "local":
            relative = os.path.relpath(absolute, side.target.root)
            permissions.resolve_path(context, relative, action)
        else:
            permissions.assert_allowed(context, action, absolute)
    except CodingError as exc:
        raise ToolError(ToolErrorCode.PERMISSION_DENIED, str(exc)) from None


async def _authorize_members(source: _Side, destination: _Side, names: list[str]) -> None:
    for side, action in ((source, "read"), (destination, "edit")):
        paths = [posixpath.join(side.path, name) if name else side.path for name in names]
        for absolute in await _resolved_paths(side, paths):
            _authorize_path(side, action, absolute)


async def _copy_files(
    runtime: Runtime,
    source_path: Annotated[str, Field(description="File or directory, relative to the source machine's root. Use '.' for the whole root.")],
    destination_path: Annotated[str, Field(description="Destination path relative to the destination root. A directory's contents are merged into it.")],
    source_target: Annotated[str | None, Field(description="Auxiliary machine ID to copy from. Omit for the main workspace.")] = None,
    destination_target: Annotated[str | None, Field(description="Auxiliary machine ID to copy to. Omit for the main workspace.")] = None,
):
    source = _Side(await resolve_target(runtime, source_target), _relative(source_path, "source_path"), runtime)
    destination = _Side(await resolve_target(runtime, destination_target),
                        _relative(destination_path, "destination_path"), runtime)
    _authorize(runtime, source, destination)
    kind, data = await _read(source)
    if len(data) > MAX_COPY_BYTES:
        raise _too_large(len(data))
    if kind == "file" and destination.path == ".":
        raise ToolError(ToolErrorCode.INVALID_INPUT, "destination_path must name the file to write.")
    members = await asyncio.to_thread(_members, data) if kind == "directory" else None
    await _authorize_members(source, destination, [name for name, _ in members] if members is not None else [""])
    files = await _write(destination, kind, data, members=members)
    return json.dumps({"copied": kind, "files": files, "bytes": len(data),
                       "source": source.label, "destination": destination.label}, ensure_ascii=False)


copy_files = register_tool(
    category=ToolCategory.FILE,
    capabilities=[ToolCapability.REQUIRES_WORKSPACE, ToolCapability.REQUIRES_THREAD, ToolCapability.WRITE_FS,
                  ToolCapability.MUTATES_STATE, ToolCapability.ASYNC_ONLY],
    tags=["coding", AUXILIARY_MACHINES_TAG],
)(StructuredTool.from_function(
    name="copy_files",
    coroutine=_copy_files,
    description=(
        "Copy a file or directory between the main workspace and an auxiliary machine of this conversation, or between "
        "two auxiliary machines. Paths are relative to each machine's root; existing files are overwritten and "
        f"directories are merged. {EXCLUDED_DIRECTORY}/ is skipped. Copy results back before deleting a machine: "
        f"its files are temporary. Limit: {MAX_COPY_BYTES // (1024 * 1024)} MiB per call."
    ),
))
