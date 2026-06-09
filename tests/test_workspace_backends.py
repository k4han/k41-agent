import json
from datetime import datetime, timedelta, timezone
from importlib.machinery import ModuleSpec
from types import SimpleNamespace
from typing import Any

import importlib.util
import pytest
from sqlalchemy import create_engine, text

import agent.modules.workspaces.local_backend as local_backend_module
import agent.modules.workspaces.service as workspace_service_module
from agent.modules.workspaces import (
    UnsupportedWorkspaceCapabilityError,
    WorkspaceRef,
    WorkspaceUnavailableError,
    get_workspace_command_executor,
    get_workspace_file_io,
    get_workspace_lifecycle_manager,
    get_workspace_repository_cloner,
    resolve_workspace_ref,
    workspace_ref_from_columns,
    workspace_ref_from_local_path,
)
from agent.modules.workspaces.daytona_backend import (
    DaytonaWorkspaceBackend,
    archive_daytona_workspace,
    daytona_relative_path,
    delete_daytona_workspace,
    resolve_daytona_path,
    stop_daytona_workspace,
    sweep_idle_daytona_workspaces,
)
from agent.modules.workspaces.local_backend import LocalWorkspaceBackend
from agent.modules.workspaces.metadata_cache import clear_workspace_metadata_cache
from agent.modules.workspaces.migrations import migrate_workspace_tables
from agent.modules.workspaces.modal_backend import (
    ModalWorkspaceBackend,
    delete_modal_workspace,
    resolve_modal_path,
)


@pytest.fixture(autouse=True)
def _clear_workspace_metadata_cache():
    clear_workspace_metadata_cache()
    yield
    clear_workspace_metadata_cache()


@pytest.fixture(autouse=True)
def _fake_optional_workspace_sdks(monkeypatch):
    # Tell the lazy integration registry that the optional daytona/modal SDKs
    # are present so ``load_backend_type`` proceeds to import the
    # ``daytona_backend`` / ``modal_backend`` modules (which don't import the
    # SDKs at module top level). The tests use fakes for the actual SDK calls
    # so the real packages are never exercised. If a future test triggers a
    # real SDK import path it will fail with ``AttributeError`` -- prefer
    # monkeypatching the backend class directly in that test.
    original_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, package: str | None = None):
        if name in {"daytona", "modal"}:
            return ModuleSpec(name, loader=None)
        return original_find_spec(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)


class FakeDaytonaFs:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {"/", "/workspace"}
        self.list_files_calls = 0

    def upload_file(self, src: bytes | str, dst: str, timeout: int = 1800) -> None:
        content = src.encode("utf-8") if isinstance(src, str) else bytes(src)
        self.files[dst] = content
        parent = dst.rsplit("/", 1)[0] or "/"
        self.dirs.add(parent)

    def download_file(self, path: str) -> bytes:
        return self.files[path]

    def list_files(self, path: str):
        self.list_files_calls += 1
        prefix = path.rstrip("/") + "/"
        entries = []
        seen: set[str] = set()
        for directory in sorted(self.dirs):
            if directory == path or not directory.startswith(prefix):
                continue
            remainder = directory[len(prefix) :]
            if "/" in remainder or not remainder:
                continue
            seen.add(remainder)
            entries.append(
                SimpleNamespace(
                    name=remainder,
                    path=directory,
                    is_dir=True,
                    size=0,
                    mod_time="2026-01-01T00:00:00Z",
                )
            )
        for file_path, content in sorted(self.files.items()):
            if not file_path.startswith(prefix):
                continue
            remainder = file_path[len(prefix) :]
            if "/" in remainder or not remainder or remainder in seen:
                continue
            entries.append(
                SimpleNamespace(
                    name=remainder,
                    path=file_path,
                    is_dir=False,
                    size=len(content),
                    mod_time="2026-01-01T00:00:00Z",
                )
            )
        return entries

    def get_file_info(self, path: str):
        if path in self.files:
            return SimpleNamespace(
                name=path.rsplit("/", 1)[-1],
                path=path,
                is_dir=False,
                size=len(self.files[path]),
                mod_time="2026-01-01T00:00:00Z",
            )
        if path in self.dirs:
            return SimpleNamespace(
                name=path.rsplit("/", 1)[-1],
                path=path,
                is_dir=True,
                size=0,
                mod_time="2026-01-01T00:00:00Z",
            )
        raise FileNotFoundError(path)


class FakeDaytonaProcess:
    def __init__(self, fs: FakeDaytonaFs, *, git_root: str | None = None) -> None:
        self.fs = fs
        self.git_root = git_root
        self.git_status_output = ""
        self.git_diff_output = ""
        self.git_numstat_output = ""
        self.commands: list[tuple[str, str | None, int | None]] = []

    def exec(self, command: str, cwd: str | None = None, timeout: int | None = None):
        self.commands.append((command, cwd, timeout))
        if "&& cd" in command and command.endswith("&& pwd"):
            return SimpleNamespace(result="/workspace\n", exit_code=0, stderr="")
        if command.startswith("mkdir -p "):
            self.fs.dirs.add(command.split("mkdir -p ", 1)[1].strip("'\""))
            return SimpleNamespace(result="", exit_code=0, stderr="")
        if command.startswith("ls -1 "):
            target = command.split("ls -1 ", 1)[1].strip("'\"")
            entries = [item.name for item in self.fs.list_files(target)]
            return SimpleNamespace(
                result="\n".join(entries) + ("\n" if entries else ""),
                exit_code=0,
                stderr="",
            )
        if command.startswith("find "):
            return SimpleNamespace(
                result="\n".join(sorted(self.fs.files))
                + ("\n" if self.fs.files else ""),
                exit_code=0,
                stderr="",
            )
        if command.startswith("git rev-parse"):
            if self.git_root is None:
                return SimpleNamespace(
                    result="", exit_code=128, stderr="not a git repo"
                )
            return SimpleNamespace(result=f"{self.git_root}\n", exit_code=0, stderr="")
        if command.startswith("git -c status.relativePaths=false status"):
            return SimpleNamespace(
                result=self.git_status_output, exit_code=0, stderr=""
            )
        if command.startswith("git diff --numstat"):
            return SimpleNamespace(
                result=self.git_numstat_output, exit_code=0, stderr=""
            )
        if command.startswith("git diff"):
            return SimpleNamespace(result=self.git_diff_output, exit_code=0, stderr="")
        if command.startswith("test -e "):
            target = command.split("test -e ", 1)[1].strip("'\"")
            exists = target in self.fs.files or target in self.fs.dirs
            return SimpleNamespace(result="", exit_code=0 if exists else 1, stderr="")
        if command.startswith("mv "):
            parts = command.split()
            source = parts[1].strip("'\"")
            destination = parts[2].strip("'\"")
            self.fs.files[destination] = self.fs.files.pop(source)
            return SimpleNamespace(result="", exit_code=0, stderr="")
        if command.startswith("if [ -d "):
            target = command.split("if [ -d ", 1)[1].split(" ];", 1)[0].strip("'\"")
            if target in self.fs.dirs:
                return SimpleNamespace(result="directory\n", exit_code=0, stderr="")
            if target in self.fs.files:
                return SimpleNamespace(result="file\n", exit_code=0, stderr="")
            return SimpleNamespace(result="", exit_code=44, stderr="")
        if command.startswith("rm -rf "):
            target = command.split("rm -rf ", 1)[1].strip("'\"")
            self.fs.files.pop(target, None)
            self.fs.dirs.discard(target)
            return SimpleNamespace(result="", exit_code=0, stderr="")
        if command.startswith("printf ") and " > " in command:
            payload, target = command.split(" > ", 1)
            content = payload.split("printf ", 1)[1].strip("'\"")
            self.fs.upload_file(content.encode("utf-8"), target.strip("'\""))
            return SimpleNamespace(result="", exit_code=0, stderr="")
        return SimpleNamespace(result="ok\n", exit_code=0, stderr="")


class FakeDaytonaSandbox:
    id = "sandbox-1"

    def __init__(
        self,
        *,
        git_root: str | None = None,
        state: str = "started",
    ) -> None:
        self.fs = FakeDaytonaFs()
        self.process = FakeDaytonaProcess(self.fs, git_root=git_root)
        self.state = state
        self.start_calls = 0
        self.stop_calls = 0
        self.archive_calls = 0
        self.delete_calls = 0
        self.recover_calls = 0

    def refresh_data(self) -> None:
        return None

    def start(self, timeout: int | None = None) -> None:
        self.start_calls += 1
        self.state = "started"

    def stop(self, timeout: int | None = None, force: bool = False) -> None:
        self.stop_calls += 1
        self.state = "stopped"

    def archive(self) -> None:
        self.archive_calls += 1
        self.state = "archived"

    def delete(self, timeout: int | None = None) -> None:
        self.delete_calls += 1
        self.state = "destroyed"

    def recover(self, timeout: int | None = None) -> None:
        self.recover_calls += 1
        self.state = "started"


class FakeModalStream:
    def __init__(self, text: str) -> None:
        self.text = text

    def read(self) -> str:
        return self.text

    class _AioRead:
        def __init__(self, parent: "FakeModalStream") -> None:
            self._parent = parent

        async def __call__(self) -> str:
            return self._parent.read()

    @property
    def aio(self):
        return self._AioRead(self)


class _AioWrapper:
    """Wraps a sync callable with an .aio async variant."""
    def __init__(self, fn: Any) -> None:
        self._fn = fn

    def __call__(self, *args, **kwargs):
        return self._fn(*args, **kwargs)

    async def aio(self, *args, **kwargs):
        return self._fn(*args, **kwargs)


class FakeModalFs:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {"/", "/workspace"}
        self.list_files_calls = 0
        self.write_text = _AioWrapper(self._write_text)
        self.write_bytes = _AioWrapper(self._write_bytes)
        self.read_text = _AioWrapper(self._read_text)
        self.read_bytes = _AioWrapper(self._read_bytes)
        self.list_files = _AioWrapper(self._list_files)
        self.stat = _AioWrapper(self._stat)
        self.make_directory = _AioWrapper(self._make_directory)

    def _make_directory(self, remote_path: str, *, create_parents: bool = True) -> None:
        if create_parents:
            current = ""
            for part in remote_path.split("/"):
                if not part:
                    current = "/"
                    continue
                current = f"{current.rstrip('/')}/{part}"
                self.dirs.add(current)
            return
        self.dirs.add(remote_path)

    def _write_text(self, data: str, remote_path: str) -> None:
        self._write_bytes(data.encode("utf-8"), remote_path)

    def _write_bytes(self, data: bytes | bytearray | memoryview, remote_path: str) -> None:
        parent = remote_path.rsplit("/", 1)[0] or "/"
        self.make_directory(parent)
        self.files[remote_path] = bytes(data)

    def _read_text(self, remote_path: str) -> str:
        return self.files[remote_path].decode("utf-8")

    def _read_bytes(self, remote_path: str) -> bytes:
        return self.files[remote_path]

    def _list_files(self, remote_path: str):
        self.list_files_calls += 1
        prefix = remote_path.rstrip("/") + "/"
        entries = []
        seen: set[str] = set()
        for directory in sorted(self.dirs):
            if directory == remote_path or not directory.startswith(prefix):
                continue
            remainder = directory[len(prefix) :]
            if "/" in remainder or not remainder:
                continue
            seen.add(remainder)
            entries.append(
                SimpleNamespace(
                    name=remainder,
                    path=directory,
                    type="directory",
                    size=0,
                    modified_time="2026-01-01T00:00:00Z",
                )
            )
        for file_path, content in sorted(self.files.items()):
            if not file_path.startswith(prefix):
                continue
            remainder = file_path[len(prefix) :]
            if "/" in remainder or not remainder or remainder in seen:
                continue
            entries.append(
                SimpleNamespace(
                    name=remainder,
                    path=file_path,
                    type="file",
                    size=len(content),
                    modified_time="2026-01-01T00:00:00Z",
                )
            )
        return entries

    def _stat(self, remote_path: str):
        if remote_path in self.files:
            return SimpleNamespace(
                name=remote_path.rsplit("/", 1)[-1],
                path=remote_path,
                type="file",
                size=len(self.files[remote_path]),
                modified_time="2026-01-01T00:00:00Z",
            )
        if remote_path in self.dirs:
            return SimpleNamespace(
                name=remote_path.rsplit("/", 1)[-1],
                path=remote_path,
                type="directory",
                size=0,
                modified_time="2026-01-01T00:00:00Z",
            )
        raise FileNotFoundError(remote_path)


class FakeModalSandbox:
    object_id = "sb-1"

    def __init__(self, *, git_root: str | None = None) -> None:
        self.filesystem = FakeModalFs()
        self.git_root = git_root
        self.git_status_output = ""
        self.git_diff_output = ""
        self.git_numstat_output = ""
        self.commands: list[tuple[str, str | None, int | None]] = []
        self.terminate_calls = 0
        self.detach_calls = 0
        self.exec = _AioWrapper(self._exec_impl)

    def _exec_impl(self, *args, timeout: int | None = None, workdir: str | None = None, **kwargs):
        del kwargs
        command = args[-1]
        self.commands.append((command, workdir, timeout))
        result = "ok\n"
        exit_code = 0
        if command.startswith("mkdir -p "):
            self.filesystem.make_directory(command.split("mkdir -p ", 1)[1].strip("'\""))
            result = ""
        elif command.startswith("ls -1 "):
            target = command.split("ls -1 ", 1)[1].strip("'\"")
            entries = [item.name for item in self.filesystem.list_files(target)]
            result = "\n".join(entries) + ("\n" if entries else "")
        elif command.startswith("find "):
            result = (
                "\n".join(sorted(self.filesystem.files))
                + ("\n" if self.filesystem.files else "")
            )
        elif command.startswith("git rev-parse"):
            if self.git_root is None:
                result = ""
                exit_code = 128
            else:
                result = f"{self.git_root}\n"
        elif command.startswith("git -c status.relativePaths=false status"):
            result = self.git_status_output
        elif command.startswith("git diff --numstat"):
            result = self.git_numstat_output
        elif command.startswith("git diff"):
            result = self.git_diff_output
        elif command.startswith("test -e "):
            target = command.split("test -e ", 1)[1].strip("'\"")
            exists = target in self.filesystem.files or target in self.filesystem.dirs
            result = ""
            exit_code = 0 if exists else 1
        elif command.startswith("mv "):
            parts = command.split()
            source = parts[1].strip("'\"")
            destination = parts[2].strip("'\"")
            self.filesystem.files[destination] = self.filesystem.files.pop(source)
            result = ""
        elif command.startswith("if [ -d "):
            target = command.split("if [ -d ", 1)[1].split(" ];", 1)[0].strip("'\"")
            if target in self.filesystem.dirs:
                result = "directory\n"
            elif target in self.filesystem.files:
                result = "file\n"
            else:
                result = ""
                exit_code = 44
        elif command.startswith("rm -rf "):
            target = command.split("rm -rf ", 1)[1].strip("'\"")
            self.filesystem.files.pop(target, None)
            self.filesystem.dirs.discard(target)
            result = ""
        elif command.startswith("printf ") and " > " in command:
            payload, target = command.split(" > ", 1)
            content = payload.split("printf ", 1)[1].strip("'\"")
            self.filesystem.write_text(content, target.strip("'\""))
            result = ""
        return SimpleNamespace(
            stdout=FakeModalStream(result),
            stderr=FakeModalStream(""),
            wait=self._make_wait(exit_code),
            returncode=exit_code,
        )

    def _make_wait(self, exit_code: int):
        def wait() -> int:
            return exit_code
        wait.aio = self._make_aio_wait(exit_code)
        return wait

    @staticmethod
    def _make_aio_wait(exit_code: int):
        async def aio_wait() -> int:
            return exit_code
        return aio_wait

    def terminate(self, *, wait: bool = False):
        del wait
        self.terminate_calls += 1
        return 0

    def detach(self) -> None:
        self.detach_calls += 1


class FakeModalUnavailableError(Exception):
    pass


class FakeUnavailableModalFs(FakeModalFs):
    def _list_files(self, path: str):
        del path
        raise FakeModalUnavailableError(
            "The Sandbox is unavailable. This Sandbox may have already shut down."
        )

    list_files = _AioWrapper(_list_files)  # type: ignore[assignment]


class FakeUnavailableModalSandbox(FakeModalSandbox):
    def _exec_impl(self, *args, timeout: int | None = None, workdir: str | None = None, **kwargs):
        del args, timeout, workdir, kwargs
        raise FakeModalUnavailableError("Task has already finished with status idle timeout")

    exec = _AioWrapper(_exec_impl)  # type: ignore[assignment]


def test_workspace_ref_normalizes_local_path(tmp_path):
    workspace = workspace_ref_from_local_path(str(tmp_path), label="repo")

    assert workspace.backend == "local"
    assert workspace.locator == str(tmp_path.resolve())
    assert workspace.label == "repo"
    assert workspace.metadata == {}
    assert resolve_workspace_ref(workspace.model_dump()) == workspace


def test_workspace_display_label_compacts_local_root(tmp_path):
    workspace = workspace_ref_from_local_path(str(tmp_path))

    assert workspace.display_label() == f"{tmp_path.name}/"


def test_workspace_display_label_disambiguates_repeated_local_root(tmp_path):
    parent = tmp_path / "k41-agent"
    nested = parent / "k41-agent"
    nested.mkdir(parents=True)

    parent_workspace = workspace_ref_from_local_path(str(parent))
    nested_workspace = workspace_ref_from_local_path(str(nested))

    assert parent_workspace.display_label() == "k41-agent/"
    assert nested_workspace.display_label() == "k41-agent/k41-agent/"


def test_workspace_display_label_keeps_custom_label(tmp_path):
    workspace = workspace_ref_from_local_path(str(tmp_path), label="octo/example")

    assert workspace.display_label() == "octo/example"


def test_workspace_ref_from_local_path_preserves_metadata(tmp_path):
    workspace = workspace_ref_from_local_path(
        str(tmp_path),
        label="repo",
        metadata={"source": "github", "branch": "main"},
    )

    assert workspace.metadata == {"source": "github", "branch": "main"}
    assert resolve_workspace_ref(workspace.model_dump()) == workspace


def test_workspace_ref_normalizes_model_instances(tmp_path):
    raw_workspace = WorkspaceRef(
        backend="local",
        locator=str(tmp_path / "nested" / ".."),
        label="repo",
        metadata={"source": "ui"},
    )

    workspace = resolve_workspace_ref(raw_workspace)

    assert workspace.locator == str(tmp_path.resolve())
    assert workspace.label == "repo"
    assert workspace.metadata == {"source": "ui"}


def test_workspace_ref_normalizes_daytona_without_resolving_local_path():
    workspace = resolve_workspace_ref(
        {
            "backend": "daytona",
            "locator": "sandbox-123",
            "metadata": {},
        }
    )

    assert workspace.backend == "daytona"
    assert workspace.locator == "sandbox-123"
    assert workspace.label == "daytona:sandbox-123"
    assert workspace.metadata["root"] == "workspace"
    assert workspace.display_label() == "daytona:sandbox-123"


def test_workspace_ref_normalizes_modal_without_resolving_local_path():
    workspace = resolve_workspace_ref(
        {
            "backend": "modal",
            "locator": "sb-123",
            "metadata": {},
        }
    )

    assert workspace.backend == "modal"
    assert workspace.locator == "sb-123"
    assert workspace.label == "modal:sb-123"
    assert workspace.metadata["root"] == "/workspace"
    assert workspace.display_label() == "modal:sb-123"


def test_workspace_ref_from_columns_preserves_daytona_backend():
    workspace = workspace_ref_from_columns(
        backend="daytona",
        locator="sandbox-123",
        label="remote",
        metadata_json='{"root": "src"}',
    )

    assert workspace.backend == "daytona"
    assert workspace.locator == "sandbox-123"
    assert workspace.label == "remote"
    assert workspace.metadata == {"root": "src"}


def test_workspace_ref_from_columns_preserves_modal_backend():
    workspace = workspace_ref_from_columns(
        backend="modal",
        locator="sb-123",
        label="remote",
        metadata_json='{"root": "/repo"}',
    )

    assert workspace.backend == "modal"
    assert workspace.locator == "sb-123"
    assert workspace.label == "remote"
    assert workspace.metadata == {"root": "/repo"}


def test_resolve_daytona_path_blocks_remote_root_escape():
    assert resolve_daytona_path("/workspace", "src/app.py") == "/workspace/src/app.py"
    assert (
        resolve_daytona_path("/workspace", "/workspace/src/app.py")
        == "/workspace/src/app.py"
    )

    with pytest.raises(ValueError, match="Path escapes workspace"):
        resolve_daytona_path("/workspace", "../secret.txt")
    with pytest.raises(ValueError, match="Path escapes workspace"):
        resolve_daytona_path("/workspace", "src/../secret.txt")
    with pytest.raises(ValueError, match="Path escapes workspace"):
        resolve_daytona_path("/workspace", "/etc/passwd")


def test_daytona_relative_path_handles_root_workspace():
    assert daytona_relative_path("/", "/") == ""
    assert daytona_relative_path("/", "/src/app.py") == "src/app.py"


def test_resolve_modal_path_blocks_remote_root_escape():
    assert resolve_modal_path("/workspace", "src/app.py") == "/workspace/src/app.py"
    assert resolve_modal_path("/workspace", "/workspace/src/app.py") == (
        "/workspace/src/app.py"
    )
    assert resolve_modal_path("/", "workspace/src/app.py") == "/workspace/src/app.py"

    with pytest.raises(ValueError, match="Path escapes workspace"):
        resolve_modal_path("/workspace", "../secret.txt")
    with pytest.raises(ValueError, match="Path escapes workspace"):
        resolve_modal_path("/workspace", "src/../secret.txt")
    with pytest.raises(ValueError, match="Path escapes workspace"):
        resolve_modal_path("/workspace", "/etc/passwd")


def test_local_workspace_backend_file_operations_and_path_guard(tmp_path):
    import asyncio

    async def _run():
        workspace = workspace_ref_from_local_path(str(tmp_path))
        backend = LocalWorkspaceBackend(workspace)
        file_path = tmp_path / "src" / "app.py"

        result = await backend.write_text("src/app.py", "print('hello')\n")

        assert result == f"[OK] Wrote file: {file_path.resolve()}"
        assert await backend.read_text("src/app.py") == "print('hello')\n"
        assert await backend.read_text(str(file_path)) == "print('hello')\n"
        assert await backend.list_dir("src") == "app.py"
        with pytest.raises(ValueError, match="Path escapes working directory"):
            await backend.read_text("../secret.txt")

    asyncio.run(_run())


def test_daytona_workspace_backend_file_operations_and_path_guard():
    import asyncio

    async def _run():
        sandbox = FakeDaytonaSandbox()
        workspace = WorkspaceRef(
            backend="daytona",
            locator="sandbox-1",
            label="sandbox",
            metadata={"root": "workspace"},
        )
        backend = DaytonaWorkspaceBackend(workspace, sandbox=sandbox)

        result = await backend.write_text("src/app.py", "print('hello')\n")

        assert result == "[OK] Wrote file: /workspace/src/app.py"
        assert await backend.read_text("src/app.py") == "print('hello')\n"
        assert await backend.list_dir("src") == "app.py"
        tree = await backend.tree()
        assert tree["root"] == "/workspace"
        assert tree["entries"][0]["path"] == "/workspace/src"
        assert (await backend.file("src/app.py"))["content"] == "print('hello')\n"
        assert (await backend.rename(path="src/app.py", new_name="main.py"))["new_path"] == (
            "/workspace/src/main.py"
        )
        assert (await backend.delete(path="src/main.py"))["kind"] == "file"
        with pytest.raises(ValueError, match="Path escapes workspace"):
            await backend.read_text("../secret.txt")

    asyncio.run(_run())


def test_modal_workspace_backend_file_operations_and_path_guard():
    import asyncio

    async def _run():
        sandbox = FakeModalSandbox()
        fs = sandbox.filesystem
        workspace = WorkspaceRef(
            backend="modal",
            locator="sb-1",
            label="sandbox",
            metadata={"root": "/workspace"},
        )
        backend = ModalWorkspaceBackend(workspace, sandbox=sandbox, fs=fs)

        result = await backend.write_text("src/app.py", "print('hello')\n")

        assert result == "[OK] Wrote file: /workspace/src/app.py"
        assert await backend.read_text("src/app.py") == "print('hello')\n"
        assert await backend.list_dir("src") == "app.py"
        tree = await backend.tree()
        assert tree["root"] == "/workspace"
        assert tree["entries"][0]["path"] == "/workspace/src"
        assert (await backend.file("src/app.py"))["content"] == "print('hello')\n"
        assert (await backend.rename(path="src/app.py", new_name="main.py"))["new_path"] == (
            "/workspace/src/main.py"
        )
        assert (await backend.delete(path="src/main.py"))["kind"] == "file"
        with pytest.raises(ValueError, match="Path escapes workspace"):
            await backend.read_text("../secret.txt")

    asyncio.run(_run())


def _count_commands(commands: list[tuple[str, str | None, int | None]], prefix: str) -> int:
    return sum(1 for command, _, _ in commands if command.startswith(prefix))


def test_daytona_workspace_backend_metadata_cache_invalidates_on_mutations():
    import asyncio

    async def _run():
        sandbox = FakeDaytonaSandbox()
        workspace = WorkspaceRef(
            backend="daytona",
            locator="sandbox-1",
            label="sandbox",
            metadata={"root": "workspace"},
        )
        backend = DaytonaWorkspaceBackend(workspace, sandbox=sandbox)
        await backend.write_text("src/app.py", "print('hello')\n")

        assert await backend.list_dir("src") == "app.py"
        first_ls_count = _count_commands(sandbox.process.commands, "ls -1 ")
        assert await backend.list_dir("src") == "app.py"
        assert _count_commands(sandbox.process.commands, "ls -1 ") == first_ls_count

        sandbox.fs.list_files_calls = 0
        first_tree = await backend.tree("src")
        second_tree = await backend.tree("src")
        assert second_tree == first_tree
        assert sandbox.fs.list_files_calls == 1

        await backend.execute("echo ok")
        assert await backend.list_dir("src") == "app.py"
        assert _count_commands(sandbox.process.commands, "ls -1 ") == first_ls_count + 1

        rename = await backend.rename(path="src/app.py", new_name="main.py")
        assert rename["new_path"] == "/workspace/src/main.py"
        assert await backend.list_dir("src") == "main.py"

        delete = await backend.delete(path="src/main.py")
        assert delete["kind"] == "file"
        assert await backend.list_dir("src") == "(Empty directory)"

    asyncio.run(_run())


def test_modal_workspace_backend_metadata_cache_invalidates_on_mutations():
    import asyncio

    async def _run():
        sandbox = FakeModalSandbox()
        workspace = WorkspaceRef(
            backend="modal",
            locator="sb-1",
            label="sandbox",
            metadata={"root": "/workspace"},
        )
        backend = ModalWorkspaceBackend(workspace, sandbox=sandbox, fs=sandbox.filesystem)
        await backend.write_text("src/app.py", "print('hello')\n")

        assert await backend.list_dir("src") == "app.py"
        first_ls_count = _count_commands(sandbox.commands, "ls -1 ")
        assert await backend.list_dir("src") == "app.py"
        assert _count_commands(sandbox.commands, "ls -1 ") == first_ls_count

        sandbox.filesystem.list_files_calls = 0
        first_tree = await backend.tree("src")
        second_tree = await backend.tree("src")
        assert second_tree == first_tree
        assert sandbox.filesystem.list_files_calls == 1

        await backend.execute("echo ok")
        assert await backend.list_dir("src") == "app.py"
        assert _count_commands(sandbox.commands, "ls -1 ") == first_ls_count + 1

        rename = await backend.rename(path="src/app.py", new_name="main.py")
        assert rename["new_path"] == "/workspace/src/main.py"
        assert await backend.list_dir("src") == "main.py"

        delete = await backend.delete(path="src/main.py")
        assert delete["kind"] == "file"
        assert await backend.list_dir("src") == "(Empty directory)"

    asyncio.run(_run())


def test_cloud_workspace_read_text_remains_uncached_after_execute():
    import asyncio

    async def _run_daytona():
        sandbox = FakeDaytonaSandbox()
        workspace = WorkspaceRef(
            backend="daytona",
            locator="sandbox-1",
            label="sandbox",
            metadata={"root": "workspace"},
        )
        backend = DaytonaWorkspaceBackend(workspace, sandbox=sandbox)
        await backend.write_text("src/app.py", "old")
        assert await backend.read_text("src/app.py") == "old"
        await backend.execute("printf updated > /workspace/src/app.py")
        assert await backend.read_text("src/app.py") == "updated"

    async def _run_modal():
        sandbox = FakeModalSandbox()
        workspace = WorkspaceRef(
            backend="modal",
            locator="sb-1",
            label="sandbox",
            metadata={"root": "/workspace"},
        )
        backend = ModalWorkspaceBackend(workspace, sandbox=sandbox, fs=sandbox.filesystem)
        await backend.write_text("src/app.py", "old")
        assert await backend.read_text("src/app.py") == "old"
        await backend.execute("printf updated > /workspace/src/app.py")
        assert await backend.read_text("src/app.py") == "updated"

    asyncio.run(_run_daytona())
    asyncio.run(_run_modal())


def test_daytona_clone_repository_uses_repo_only_relative_path():
    import asyncio

    async def _run():
        sandbox = FakeDaytonaSandbox()
        workspace = WorkspaceRef(
            backend="daytona",
            locator="sandbox-1",
            label="sandbox",
            metadata={"root": "workspace"},
        )
        backend = DaytonaWorkspaceBackend(workspace, sandbox=sandbox)

        relative = backend.clone_repository(
            owner="acme",
            repo="widgets",
            default_branch="main",
            token="install-token",
        )

        assert relative == "widgets"
        clone_commands = [
            cmd for cmd, _, _ in sandbox.process.commands if cmd.startswith("git clone")
        ]
        assert clone_commands, sandbox.process.commands
        for cmd in clone_commands:
            assert cmd.rstrip().endswith("/workspace/widgets")

    asyncio.run(_run())


def test_modal_clone_repository_uses_repo_only_relative_path():
    import asyncio

    async def _run():
        sandbox = FakeModalSandbox()
        fs = sandbox.filesystem
        workspace = WorkspaceRef(
            backend="modal",
            locator="sb-1",
            label="sandbox",
            metadata={"root": "/workspace"},
        )
        backend = ModalWorkspaceBackend(workspace, sandbox=sandbox, fs=fs)

        relative = await backend.clone_repository(
            owner="acme",
            repo="widgets",
            default_branch="main",
            token="install-token",
        )

        assert relative == "widgets"
        clone_commands = [
            cmd for cmd, _, _ in sandbox.commands if cmd.startswith("git clone")
        ]
        assert clone_commands, sandbox.commands
        for cmd in clone_commands:
            assert cmd.rstrip().endswith("/workspace/widgets")

    asyncio.run(_run())


def test_daytona_workspace_backend_starts_stopped_sandbox():
    sandbox = FakeDaytonaSandbox(state="stopped")
    workspace = WorkspaceRef(
        backend="daytona",
        locator="sandbox-1",
        label="sandbox",
        metadata={"root": "workspace"},
    )

    backend = DaytonaWorkspaceBackend(workspace, sandbox=sandbox)

    assert backend.status == "started"
    assert sandbox.start_calls == 1
    assert workspace.metadata["status"] == "started"
    assert workspace.metadata["root"] == "workspace"
    assert workspace.metadata["last_used_at"]
    assert workspace.metadata["last_started_at"]


def test_daytona_lifecycle_helpers_stop_and_archive_sandbox(monkeypatch):
    sandbox = FakeDaytonaSandbox(state="started")
    workspace = WorkspaceRef(
        backend="daytona",
        locator="sandbox-1",
        label="sandbox",
        metadata={"root": "workspace"},
    )

    lifecycle_calls: list[dict[str, str | None]] = []

    def fake_update_daytona_thread_lifecycle_sync(
        thread_id,
        *,
        root=None,
        status=None,
        touch=False,
        started=False,
        stopped=False,
        archived=False,
    ):
        lifecycle_calls.append(
            {
                "thread_id": thread_id,
                "root": root,
                "status": status,
                "touch": touch,
                "started": started,
                "stopped": stopped,
                "archived": archived,
            }
        )

    monkeypatch.setattr(
        "agent.modules.workspaces.daytona_backend.get_daytona_sandbox",
        lambda ref: sandbox,
    )
    monkeypatch.setattr(
        "agent.modules.workspaces.daytona_backend.update_daytona_thread_lifecycle_sync",
        fake_update_daytona_thread_lifecycle_sync,
    )

    thread_id = "thread-lifecycle"
    assert stop_daytona_workspace(workspace, thread_id=thread_id) == "stopped"
    assert sandbox.stop_calls == 1
    assert workspace.metadata["status"] == "stopped"
    assert workspace.metadata["last_stopped_at"]

    assert archive_daytona_workspace(workspace, thread_id=thread_id) == "archived"
    assert sandbox.archive_calls == 1
    assert workspace.metadata["status"] == "archived"
    assert workspace.metadata["last_archived_at"]

    assert delete_daytona_workspace(workspace, thread_id=thread_id) == "destroyed"
    assert sandbox.delete_calls == 1
    assert workspace.metadata["status"] == "destroyed"

    # Each lifecycle helper should forward the thread id to the persistence
    # update so the sweeper/stop/archive hooks stay attached to the right
    # thread workspace record.
    assert [call["thread_id"] for call in lifecycle_calls] == [
        thread_id,
        thread_id,
        thread_id,
    ]
    assert lifecycle_calls[0]["status"] == "stopped"
    assert lifecycle_calls[0]["stopped"] is True
    assert lifecycle_calls[1]["status"] == "archived"
    assert lifecycle_calls[1]["archived"] is True
    assert lifecycle_calls[2]["status"] == "destroyed"


def test_delete_modal_workspace_terminates_and_detaches_sandbox(monkeypatch):
    import asyncio

    sandbox = FakeModalSandbox()
    workspace = WorkspaceRef(
        backend="modal",
        locator="sb-1",
        label="sandbox",
        metadata={"root": "/workspace"},
    )

    async def fake_get_modal_sandbox(ref, *, client=None):
        return sandbox

    monkeypatch.setattr(
        "agent.modules.workspaces.modal_backend.get_modal_sandbox",
        fake_get_modal_sandbox,
    )

    async def _run():
        assert await delete_modal_workspace(workspace) == "terminated"

    asyncio.run(_run())
    assert sandbox.terminate_calls == 1
    assert sandbox.detach_calls == 1


def test_delete_modal_workspace_handles_invalid_sandbox_id(monkeypatch):
    import asyncio

    workspace = WorkspaceRef(
        backend="modal",
        locator="github:k4han/test_code_fix",
        label="sandbox",
        metadata={"root": "/workspace"},
    )

    async def fake_get_modal_sandbox(ref, *, client=None):
        raise Exception("github:k4han/test_code_fix is not a valid Sandbox ID")

    monkeypatch.setattr(
        "agent.modules.workspaces.modal_backend.get_modal_sandbox",
        fake_get_modal_sandbox,
    )

    async def _run():
        assert await delete_modal_workspace(workspace) == "terminated"

    asyncio.run(_run())


def test_modal_backend_reports_unavailable_sandbox():
    import asyncio

    workspace = resolve_workspace_ref(
        {
            "backend": "modal",
            "locator": "sb-expired",
            "metadata": {"root": "/workspace"},
        }
    )
    sandbox = FakeModalSandbox()
    sandbox.filesystem = FakeUnavailableModalFs()
    backend = ModalWorkspaceBackend(workspace, sandbox=sandbox, fs=sandbox.filesystem)

    async def _run():
        with pytest.raises(WorkspaceUnavailableError) as exc_info:
            await backend.tree()
        return exc_info

    exc_info = asyncio.run(_run())
    assert exc_info.value.backend == "modal"
    assert exc_info.value.locator == "sb-expired"
    assert "no longer available" in str(exc_info.value)


def test_modal_backend_changes_reports_unavailable_sandbox():
    import asyncio

    workspace = resolve_workspace_ref(
        {
            "backend": "modal",
            "locator": "sb-expired",
            "metadata": {"root": "/workspace"},
        }
    )
    backend = ModalWorkspaceBackend(workspace, sandbox=FakeUnavailableModalSandbox())

    async def _run():
        with pytest.raises(WorkspaceUnavailableError):
            await backend.changes()

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_modal_capability_recovers_unavailable_thread_workspace(monkeypatch):
    from agent.modules.workspaces import service as workspace_service

    workspace_service._modal_recovery_locks.clear()
    expired = WorkspaceRef(
        backend="modal",
        locator="sb-expired",
        label="acme/widgets",
        metadata={
            "root": "/workspace",
            "source": "github",
            "repository_id": 44,
            "repository_full_name": "acme/widgets",
            "default_branch": "main",
            "repository_path": "acme/widgets",
        },
    )
    created = WorkspaceRef(
        backend="modal",
        locator="sb-new",
        label="acme/widgets",
        metadata={"root": "/workspace", "app_name": "k41-agent-sandboxes"},
    )
    stored: dict[str, WorkspaceRef] = {"thread-old": expired}
    upserts: list[WorkspaceRef] = []
    create_labels: list[str | None] = []
    clone_calls: list[dict[str, Any]] = []
    fresh_sandbox = FakeModalSandbox()

    class FakeWorkspaceRepository:
        async def get(self, thread_id: str):
            workspace = stored.get(thread_id)
            return {"workspace": workspace.model_dump()} if workspace else None

        async def upsert(self, *, thread_id: str, workspace):
            stored[thread_id] = workspace
            upserts.append(workspace)
            return {"workspace": workspace.model_dump()}

    async def fake_create_modal_workspace(*, label: str | None = None):
        create_labels.append(label)
        return created

    async def fake_create_backend(ref: WorkspaceRef, *, client=None):
        del client
        if ref.locator == "sb-expired":
            return ModalWorkspaceBackend(
                ref,
                sandbox=FakeUnavailableModalSandbox(),
                fs=FakeUnavailableModalFs(),
            )
        if ref.locator == "sb-new":
            return ModalWorkspaceBackend(
                ref,
                sandbox=fresh_sandbox,
                fs=fresh_sandbox.filesystem,
            )
        raise AssertionError(f"Unexpected Modal sandbox: {ref.locator}")

    async def fake_attach_github_repository_to_workspace(
        workspace: WorkspaceRef,
        *,
        repository_id: int,
        install_token: str | None = None,
    ) -> WorkspaceRef:
        clone_calls.append(
            {
                "locator": workspace.locator,
                "repository_id": repository_id,
                "install_token": install_token,
            }
        )
        metadata = dict(workspace.metadata)
        metadata.update(
            {
                "source": "github",
                "repository_id": repository_id,
                "repository_full_name": "acme/widgets",
                "default_branch": "main",
                "repository_path": "acme/widgets",
            }
        )
        return WorkspaceRef(
            backend=workspace.backend,
            locator=workspace.locator,
            label="acme/widgets",
            metadata=metadata,
        )

    monkeypatch.setattr(
        workspace_service,
        "get_thread_workspace_repository",
        lambda: FakeWorkspaceRepository(),
    )
    monkeypatch.setattr(
        "agent.modules.workspaces.modal_backend.create_modal_workspace",
        fake_create_modal_workspace,
    )
    monkeypatch.setattr(
        ModalWorkspaceBackend,
        "create",
        staticmethod(fake_create_backend),
    )
    monkeypatch.setattr(
        "agent.modules.workspaces.github_clone.attach_github_repository_to_workspace",
        fake_attach_github_repository_to_workspace,
    )

    browser = await workspace_service.get_workspace_browser(
        expired,
        thread_id="thread-old",
    )
    result = await browser.tree()

    assert result["root"] == "/workspace"
    assert browser.ref.locator == "sb-new"
    assert create_labels == ["acme/widgets"]
    assert clone_calls == [
        {
            "locator": "sb-new",
            "repository_id": 44,
            "install_token": None,
        }
    ]
    assert upserts == [stored["thread-old"]]
    assert stored["thread-old"].locator == "sb-new"
    assert stored["thread-old"].metadata["source"] == "github"
    assert stored["thread-old"].metadata["repository_full_name"] == "acme/widgets"


@pytest.mark.asyncio
async def test_delete_thread_workspace_deletes_daytona_sandbox_and_record(monkeypatch):
    from agent.modules.workspaces import service as workspace_service

    workspace = WorkspaceRef(
        backend="daytona",
        locator="sandbox-1",
        label="sandbox",
        metadata={"root": "workspace"},
    )
    calls: list[tuple[str, str]] = []

    class FakeWorkspaceRepository:
        async def get(self, thread_id: str):
            calls.append(("get", thread_id))
            return {"workspace": workspace.model_dump()}

        async def delete(self, thread_id: str):
            calls.append(("delete_record", thread_id))
            return True

    def fake_delete_daytona_workspace(ref: WorkspaceRef, *, thread_id: str | None = None):
        calls.append(("delete_sandbox", f"{thread_id}:{ref.locator}"))
        return "destroyed"

    monkeypatch.setattr(
        workspace_service,
        "get_thread_workspace_repository",
        lambda: FakeWorkspaceRepository(),
    )
    monkeypatch.setattr(
        "agent.modules.workspaces.daytona_backend.delete_daytona_workspace",
        fake_delete_daytona_workspace,
    )

    result = await workspace_service.delete_thread_workspace("thread-daytona")

    assert result == workspace
    assert calls == [
        ("get", "thread-daytona"),
        ("delete_sandbox", "thread-daytona:sandbox-1"),
        ("delete_record", "thread-daytona"),
    ]


@pytest.mark.asyncio
async def test_delete_thread_workspace_deletes_modal_sandbox_and_record(monkeypatch):
    from agent.modules.workspaces import service as workspace_service

    workspace = WorkspaceRef(
        backend="modal",
        locator="sb-1",
        label="sandbox",
        metadata={"root": "/workspace"},
    )
    calls: list[tuple[str, str]] = []

    class FakeWorkspaceRepository:
        async def get(self, thread_id: str):
            calls.append(("get", thread_id))
            return {"workspace": workspace.model_dump()}

        async def delete(self, thread_id: str):
            calls.append(("delete_record", thread_id))
            return True

    async def fake_delete_modal_workspace(ref: WorkspaceRef):
        calls.append(("delete_sandbox", ref.locator))
        return "terminated"

    monkeypatch.setattr(
        workspace_service,
        "get_thread_workspace_repository",
        lambda: FakeWorkspaceRepository(),
    )
    monkeypatch.setattr(
        "agent.modules.workspaces.modal_backend.delete_modal_workspace",
        fake_delete_modal_workspace,
    )

    result = await workspace_service.delete_thread_workspace("thread-modal")

    assert result == workspace
    assert calls == [
        ("get", "thread-modal"),
        ("delete_sandbox", "sb-1"),
        ("delete_record", "thread-modal"),
    ]


@pytest.mark.asyncio
async def test_daytona_lifecycle_sweeper_stops_and_archives_idle_workspaces(
    monkeypatch,
):
    now = datetime(2026, 1, 8, tzinfo=timezone.utc)
    stopped: list[str] = []
    archived: list[str] = []
    closed: list[str] = []

    class FakeWorkspaceRepository:
        async def list_by_backend(self, backend: str):
            assert backend == "daytona"
            return {
                "thread-stop": {
                    "workspace": {
                        "backend": "daytona",
                        "locator": "sandbox-stop",
                        "label": "stop",
                        "metadata": {
                            "root": "workspace",
                            "status": "started",
                            "last_used_at": (now - timedelta(minutes=31)).isoformat(),
                        },
                    },
                },
                "thread-archive": {
                    "workspace": {
                        "backend": "daytona",
                        "locator": "sandbox-archive",
                        "label": "archive",
                        "metadata": {
                            "root": "workspace",
                            "status": "stopped",
                            "last_used_at": (now - timedelta(days=8)).isoformat(),
                        },
                    },
                },
                "thread-fresh": {
                    "workspace": {
                        "backend": "daytona",
                        "locator": "sandbox-fresh",
                        "label": "fresh",
                        "metadata": {
                            "root": "workspace",
                            "status": "started",
                            "last_used_at": (now - timedelta(minutes=5)).isoformat(),
                        },
                    },
                },
            }

    monkeypatch.setattr(
        "agent.modules.workspaces.daytona_backend._config",
        lambda: (True, "key", "workspace"),
    )
    monkeypatch.setattr(
        "agent.modules.workspaces.daytona_backend._lifecycle_config",
        lambda: {
            "auto_stop_minutes": 30,
            "auto_archive_days": 7,
            "sweeper_interval_seconds": 60,
            "start_timeout_seconds": 120,
            "stop_timeout_seconds": 60,
        },
    )
    monkeypatch.setattr(
        "agent.modules.workspaces.repository.get_thread_workspace_repository",
        lambda: FakeWorkspaceRepository(),
    )
    monkeypatch.setattr(
        "agent.modules.tools.close_thread_shell_sessions",
        lambda thread_id: closed.append(thread_id) or 0,
    )
    monkeypatch.setattr(
        "agent.modules.workspaces.daytona_backend.stop_daytona_workspace",
        lambda workspace, thread_id=None: stopped.append(thread_id or "") or "stopped",
    )
    monkeypatch.setattr(
        "agent.modules.workspaces.daytona_backend.archive_daytona_workspace",
        lambda workspace, thread_id=None: (
            archived.append(thread_id or "") or "archived"
        ),
    )

    result = await sweep_idle_daytona_workspaces(now=now)

    assert result["checked"] == 3
    assert result["stopped"] == 1
    assert result["archived"] == 1
    assert result["skipped"] == 1
    assert stopped == ["thread-stop"]
    assert archived == ["thread-archive"]
    assert closed == ["thread-stop", "thread-archive"]


@pytest.mark.asyncio
async def test_daytona_sweeper_marks_not_found_as_destroyed(monkeypatch):
    now = datetime(2026, 1, 8, tzinfo=timezone.utc)
    lifecycle_updates: list[dict[str, Any]] = []

    class FakeWorkspaceRepository:
        async def list_by_backend(self, backend: str):
            assert backend == "daytona"
            return {
                "thread-gone": {
                    "workspace": {
                        "backend": "daytona",
                        "locator": "sandbox-gone",
                        "label": "gone",
                        "metadata": {
                            "root": "workspace",
                            "status": "started",
                            "last_used_at": (now - timedelta(minutes=31)).isoformat(),
                        },
                    },
                },
            }

    monkeypatch.setattr(
        "agent.modules.workspaces.daytona_backend._config",
        lambda: (True, "key", "workspace"),
    )
    monkeypatch.setattr(
        "agent.modules.workspaces.daytona_backend._lifecycle_config",
        lambda: {
            "auto_stop_minutes": 30,
            "auto_archive_days": 7,
            "sweeper_interval_seconds": 60,
            "start_timeout_seconds": 120,
            "stop_timeout_seconds": 60,
        },
    )
    monkeypatch.setattr(
        "agent.modules.workspaces.repository.get_thread_workspace_repository",
        lambda: FakeWorkspaceRepository(),
    )
    monkeypatch.setattr(
        "agent.modules.workspaces.daytona_backend.stop_daytona_workspace",
        lambda workspace, thread_id=None: (_ for _ in ()).throw(
            RuntimeError("Sandbox with ID or name sandbox-gone not found")
        ),
    )
    monkeypatch.setattr(
        "agent.modules.tools.close_thread_shell_sessions",
        lambda thread_id: 0,
    )
    monkeypatch.setattr(
        "agent.modules.workspaces.daytona_backend.update_daytona_thread_lifecycle_sync",
        lambda thread_id, **kw: lifecycle_updates.append(kw),
    )

    result = await sweep_idle_daytona_workspaces(now=now)

    assert result["errors"] == [], f"Unexpected errors: {result['errors']}"
    assert result["skipped"] == 1
    assert result["stopped"] == 0
    assert result["archived"] == 0
    assert len(lifecycle_updates) == 1
    assert lifecycle_updates[0]["status"] == "destroyed"


def test_local_workspace_backend_execute_uses_safe_workspace(
    monkeypatch,
    tmp_path,
):
    import asyncio

    workspace = workspace_ref_from_local_path(str(tmp_path))
    backend = LocalWorkspaceBackend(workspace)
    captured = {}

    class FakePopen:
        pid = 12345
        returncode = 0

        def __init__(self, command, **kwargs):
            captured["command"] = command
            captured.update(kwargs)

        def communicate(self, timeout=None):
            captured["timeout"] = timeout
            return "ok", ""

        def kill(self):
            return None

    monkeypatch.setattr(local_backend_module.subprocess, "Popen", FakePopen)

    async def _run():
        return await backend.execute("echo ok", timeout=12)

    result = asyncio.run(_run())

    assert result.output == "ok"
    assert result.exit_code == 0
    assert captured["command"] == "echo ok"
    assert captured["cwd"] == str(tmp_path.resolve())
    assert captured["timeout"] == 12
    if hasattr(local_backend_module.subprocess, "CREATE_NO_WINDOW"):
        assert (
            captured["creationflags"]
            & local_backend_module.subprocess.CREATE_NO_WINDOW
        )
        assert (
            captured["startupinfo"].wShowWindow
            == local_backend_module.subprocess.SW_HIDE
        )


def test_workspace_git_commands_hide_console_on_windows(monkeypatch, tmp_path):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(workspace_service_module.subprocess, "run", fake_run)

    workspace_service_module._run_git(["status"], cwd=tmp_path)

    assert captured["command"] == ["git", "status"]
    if hasattr(workspace_service_module.subprocess, "CREATE_NO_WINDOW"):
        assert (
            captured["creationflags"]
            & workspace_service_module.subprocess.CREATE_NO_WINDOW
        )
        assert (
            captured["startupinfo"].wShowWindow
            == workspace_service_module.subprocess.SW_HIDE
        )


def test_daytona_workspace_backend_execute_changes_and_untracked_diff():
    import asyncio

    sandbox = FakeDaytonaSandbox(git_root="/workspace")
    sandbox.fs.upload_file(b"hello\nworld\n", "/workspace/notes.txt")
    sandbox.process.git_status_output = "?? notes.txt\0"
    workspace = WorkspaceRef(
        backend="daytona",
        locator="sandbox-1",
        label="sandbox",
        metadata={"root": "workspace"},
    )
    backend = DaytonaWorkspaceBackend(workspace, sandbox=sandbox)

    async def _run():
        result = await backend.execute("echo ok", timeout=12)
        changes = await backend.changes()
        diff = await backend.diff("/workspace/notes.txt")
        return result, changes, diff

    result, changes, diff = asyncio.run(_run())

    assert result.output == "ok\n"
    assert result.exit_code == 0
    assert ("echo ok", "/workspace", 12) in sandbox.process.commands
    assert changes["is_git_repo"] is True
    assert changes["changes"][0]["path"] == "/workspace/notes.txt"
    assert changes["changes"][0]["status"] == "untracked"
    assert changes["changes"][0]["additions"] == 2
    assert "+hello" in diff["diff"]
    assert "+world" in diff["diff"]
    assert _count_commands(
        sandbox.process.commands,
        "git -c status.relativePaths=false status",
    ) == 1


def test_daytona_workspace_backend_changes_handles_non_git_workspace():
    import asyncio

    sandbox = FakeDaytonaSandbox(git_root=None)
    workspace = WorkspaceRef(
        backend="daytona",
        locator="sandbox-1",
        label="sandbox",
        metadata={"root": "workspace"},
    )
    backend = DaytonaWorkspaceBackend(workspace, sandbox=sandbox)

    async def _run():
        return await backend.changes()

    assert asyncio.run(_run()) == {
        "root": "/workspace",
        "is_git_repo": False,
        "changes": [],
        "message": "Workspace is not a Git repository.",
    }


def test_daytona_workspace_backend_changes_batches_tracked_line_stats():
    import asyncio

    sandbox = FakeDaytonaSandbox(git_root="/workspace")
    sandbox.process.git_status_output = " M app.py\0 M other.py\0"
    sandbox.process.git_numstat_output = "1\t0\tapp.py\n2\t0\tother.py\n"
    workspace = WorkspaceRef(
        backend="daytona",
        locator="sandbox-1",
        label="sandbox",
        metadata={"root": "workspace"},
    )
    backend = DaytonaWorkspaceBackend(workspace, sandbox=sandbox)

    async def _run():
        return await backend.changes()

    changes = asyncio.run(_run())

    assert [change["path"] for change in changes["changes"]] == [
        "/workspace/app.py",
        "/workspace/other.py",
    ]
    assert _count_commands(sandbox.process.commands, "git diff --numstat") == 2


def test_modal_workspace_backend_execute_changes_and_untracked_diff():
    import asyncio

    sandbox = FakeModalSandbox(git_root="/workspace")
    sandbox.filesystem.write_text("hello\nworld\n", "/workspace/notes.txt")
    sandbox.git_status_output = "?? notes.txt\0"
    workspace = WorkspaceRef(
        backend="modal",
        locator="sb-1",
        label="sandbox",
        metadata={"root": "/workspace"},
    )
    backend = ModalWorkspaceBackend(workspace, sandbox=sandbox, fs=sandbox.filesystem)

    async def _run():
        result = await backend.execute("echo ok", timeout=12)
        changes = await backend.changes()
        diff = await backend.diff("/workspace/notes.txt")
        return result, changes, diff

    result, changes, diff = asyncio.run(_run())

    assert result.output == "ok\n"
    assert result.exit_code == 0
    assert ("echo ok", "/workspace", 12) in sandbox.commands
    assert changes["is_git_repo"] is True
    assert changes["changes"][0]["path"] == "/workspace/notes.txt"
    assert changes["changes"][0]["status"] == "untracked"
    assert changes["changes"][0]["additions"] == 2
    assert "+hello" in diff["diff"]
    assert "+world" in diff["diff"]
    assert _count_commands(
        sandbox.commands,
        "git -c status.relativePaths=false status",
    ) == 1


def test_modal_workspace_backend_changes_batches_tracked_line_stats():
    import asyncio

    sandbox = FakeModalSandbox(git_root="/workspace")
    sandbox.git_status_output = " M app.py\0 M other.py\0"
    sandbox.git_numstat_output = "1\t0\tapp.py\n2\t0\tother.py\n"
    workspace = WorkspaceRef(
        backend="modal",
        locator="sb-1",
        label="sandbox",
        metadata={"root": "/workspace"},
    )
    backend = ModalWorkspaceBackend(workspace, sandbox=sandbox, fs=sandbox.filesystem)

    async def _run():
        return await backend.changes()

    changes = asyncio.run(_run())

    assert [change["path"] for change in changes["changes"]] == [
        "/workspace/app.py",
        "/workspace/other.py",
    ]
    assert _count_commands(sandbox.commands, "git diff --numstat") == 2


def test_workspace_migration_backfills_legacy_tables(tmp_path):
    db_path = tmp_path / "legacy.db"
    database_url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE thread_workspaces ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "thread_id VARCHAR(512), "
                    "working_dir TEXT, "
                    "created_at TEXT, "
                    "updated_at TEXT)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO thread_workspaces "
                    "(thread_id, working_dir, created_at, updated_at) "
                    "VALUES ('thread-1', :working_dir, '', '')"
                ),
                {"working_dir": str(tmp_path)},
            )
            conn.execute(
                text(
                    "CREATE TABLE background_tasks ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "thread_id VARCHAR(512), "
                    "working_dir TEXT, "
                    "created_at TEXT, "
                    "updated_at TEXT)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO background_tasks "
                    "(thread_id, working_dir, created_at, updated_at) "
                    "VALUES ('task-1', :working_dir, '', '')"
                ),
                {"working_dir": str(tmp_path / "task")},
            )
    finally:
        engine.dispose()

    migrate_workspace_tables(database_url)

    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT workspace_backend, workspace_locator, "
                    "workspace_label, workspace_metadata_json "
                    "FROM thread_workspaces WHERE thread_id = 'thread-1'"
                )
            ).one()
            task_row = conn.execute(
                text(
                    "SELECT workspace_backend, workspace_locator, "
                    "workspace_label, workspace_metadata_json "
                    "FROM background_tasks WHERE thread_id = 'task-1'"
                )
            ).one()
    finally:
        engine.dispose()

    assert row.workspace_backend == "local"
    assert row.workspace_locator == str(tmp_path)
    assert row.workspace_label == str(tmp_path)
    assert json.loads(row.workspace_metadata_json) == {}
    assert task_row.workspace_backend == "local"
    assert task_row.workspace_locator == str(tmp_path / "task")
    assert task_row.workspace_label == str(tmp_path / "task")
    assert json.loads(task_row.workspace_metadata_json) == {}


def test_get_workspace_file_io_returns_physical_local_backend(tmp_path):
    import asyncio

    workspace = workspace_ref_from_local_path(str(tmp_path), label="test-lab")

    async def _run():
        return await get_workspace_file_io(workspace)

    backend = asyncio.run(_run())

    assert isinstance(backend, LocalWorkspaceBackend)
    assert not hasattr(backend, "virtual_prefix")


def test_local_workspace_unsupported_capabilities(tmp_path):
    import asyncio

    workspace = workspace_ref_from_local_path(str(tmp_path), label="test-lab")

    async def _repository_cloner():
        return await get_workspace_repository_cloner(workspace)

    async def _lifecycle_manager():
        return await get_workspace_lifecycle_manager(workspace)

    with pytest.raises(UnsupportedWorkspaceCapabilityError) as clone_exc:
        asyncio.run(_repository_cloner())
    with pytest.raises(UnsupportedWorkspaceCapabilityError) as lifecycle_exc:
        asyncio.run(_lifecycle_manager())

    assert clone_exc.value.backend == "local"
    assert clone_exc.value.capability == "repository clone"
    assert lifecycle_exc.value.backend == "local"
    assert lifecycle_exc.value.capability == "lifecycle"


def test_get_workspace_command_executor_returns_modal_backend(monkeypatch):
    import asyncio

    sandbox = FakeModalSandbox()
    workspace = WorkspaceRef(
        backend="modal",
        locator="sb-1",
        label="sandbox",
        metadata={"root": "/workspace"},
    )

    async def fake_get_modal_sandbox(ref, *, client=None):
        return sandbox

    async def fake_get_modal_client():
        return None

    monkeypatch.setattr(
        "agent.modules.workspaces.modal_backend.get_modal_sandbox",
        fake_get_modal_sandbox,
    )
    monkeypatch.setattr(
        "agent.modules.workspaces.modal_backend.get_modal_client",
        fake_get_modal_client,
    )

    async def _run():
        return await get_workspace_command_executor(workspace)

    backend = asyncio.run(_run())

    assert isinstance(backend, ModalWorkspaceBackend)


def test_local_workspace_tree_uses_absolute_path_keys(tmp_path):
    import asyncio

    workspace = workspace_ref_from_local_path(str(tmp_path), label="test-lab")
    backend = LocalWorkspaceBackend(workspace)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')", encoding="utf-8")

    async def _run():
        root_tree = await backend.tree()
        src_tree = await backend.tree(root_tree["entries"][0]["path"])
        return root_tree, src_tree

    root_tree, src_tree = asyncio.run(_run())

    assert root_tree["path"] == str(tmp_path.resolve())
    assert root_tree["entries"][0]["path"] == str((tmp_path / "src").resolve())

    assert src_tree["path"] == str((tmp_path / "src").resolve())
    assert src_tree["entries"][0]["path"] == str(
        (tmp_path / "src" / "main.py").resolve()
    )
