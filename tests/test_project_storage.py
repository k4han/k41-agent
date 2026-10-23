"""Durable storage contracts and portable filesystem checkpoint behavior."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import shlex
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from agent.modules.projects.snapshot_worker import CHUNK_BYTES, capture
from agent.modules.projects.storage import LocalProjectStorage, checksum, file_chunks, manifest_bytes, validate_manifest
from agent.modules.projects.transfer import WorkspaceSnapshotTransfer
from agent.modules.workspaces.refs import WorkspaceRef


class MemoryObjectStorage:
    def __init__(self):
        self.blobs, self.manifests = {}, {}

    def has_blob(self, workspace, digest):
        return (workspace, digest) in self.blobs

    def put_blob(self, workspace, digest, content):
        if checksum(content) != digest:
            raise ValueError("Checksum mismatch")
        self.blobs[workspace, digest] = content

    def get_blob(self, workspace, digest):
        content = self.blobs[workspace, digest]
        if checksum(content) != digest:
            raise ValueError("Checksum mismatch")
        return content

    def put_manifest(self, workspace, manifest):
        content = manifest_bytes(manifest)
        for entry in manifest["entries"].values():
            if entry["type"] == "file":
                content_bytes = b"".join(self.get_blob(workspace, chunk["checksum"]) for chunk in file_chunks(entry))
                if checksum(content_bytes) != entry["checksum"]:
                    raise ValueError("Checksum mismatch")
        revision = checksum(content)
        self.manifests[workspace, revision] = json.loads(content)
        return revision

    def get_manifest(self, workspace, revision):
        return self.manifests[workspace, revision]

    def collect(self, workspace, revisions):
        return None


@pytest.fixture(params=["local", "objects"])
def store(request, tmp_path):
    return LocalProjectStorage(tmp_path / "store") if request.param == "local" else MemoryObjectStorage()


@pytest.mark.asyncio
async def test_storage_round_trip_chunks_untracked_files_and_exclusions(store, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    binary = bytes(range(256)) * (CHUNK_BYTES // 256 + 17)
    (source / "payload.bin").write_bytes(binary)
    (source / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
    (source / ".gitignore").write_text("data/\n")
    for folder in ("data", "dist", "node_modules", ".venv", "__pycache__", ".agents/skills/example",
                   ".k41-agent/scratchpad", ".k41-agent/outputs", ".k41-agent/package-runtime"):
        path = source / folder
        path.mkdir(parents=True)
        (path / "item.txt").write_text(folder)
    transfer = WorkspaceSnapshotTransfer()
    manifest = await transfer.capture(WorkspaceRef(locator=str(source)), store, "1")
    revision = store.put_manifest("1", manifest)
    assert len(manifest["entries"]["payload.bin"]["chunks"]) == 2
    assert "data/item.txt" in manifest["entries"]
    assert "dist/item.txt" in manifest["entries"]
    assert ".agents/skills/example/item.txt" in manifest["entries"]
    assert ".k41-agent/scratchpad/item.txt" in manifest["entries"]
    assert not any(name.startswith(("node_modules", ".venv", "__pycache__", ".k41-agent/outputs", ".k41-agent/package-runtime"))
                   for name in manifest["entries"])
    destination = tmp_path / "restored"
    transfer.materialize(store, "1", revision, destination)
    assert (destination / "payload.bin").read_bytes() == binary
    assert (destination / "AGENTS.md").read_text() == "# Project\n"
    assert store.put_manifest("1", await transfer.capture(WorkspaceRef(locator=str(source)), store, "1")) == revision
    (source / "AGENTS.md").rename(source / "instructions.md")
    changed = await transfer.capture(WorkspaceRef(locator=str(source)), store, "1")
    assert "AGENTS.md" not in changed["entries"] and "instructions.md" in changed["entries"]


def test_storage_rejects_incomplete_or_corrupt_publication(tmp_path):
    store = LocalProjectStorage(tmp_path)
    digest = checksum(b"content")
    manifest = {"version": 1, "entries": {"file": {"type": "file", "checksum": digest, "size": 7, "mode": 0o644}}}
    with pytest.raises(FileNotFoundError):
        store.put_manifest("1", manifest)
    store.put_blob("1", digest, b"content")
    revision = store.put_manifest("1", manifest)
    (tmp_path / "1/objects" / digest).write_bytes(b"damaged")
    with pytest.raises(ValueError, match="checksum"):
        store.get_blob("1", digest)
    assert store.get_manifest("1", revision) == manifest


@pytest.mark.parametrize("path", ["../outside", "/absolute", "C:/absolute", "a/../../outside", "a\\b"])
def test_manifest_rejects_unsafe_paths(path):
    with pytest.raises(ValueError):
        validate_manifest({"version": 1, "entries": {path: {"type": "directory"}}})


def test_storage_namespace_survives_database_driver_and_credential_changes(tmp_path):
    from agent.modules.projects.workspaces import database_storage_namespace
    assert database_storage_namespace("postgresql+asyncpg://user:old@host/app") == database_storage_namespace("postgresql://user:new@host:5432/app")
    assert database_storage_namespace("postgresql://user:password@host/other") != database_storage_namespace("postgresql://user:password@host/app")
    path = (tmp_path / "app.db").as_posix()
    assert database_storage_namespace("sqlite:///" + path) == database_storage_namespace("sqlite+aiosqlite:///" + path)


def test_capture_rejects_external_links(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "link").symlink_to("../outside")
    except OSError:
        pytest.skip("Symlinks require platform support.")
    with tempfile.TemporaryDirectory(prefix="k41-project-") as stage:
        with pytest.raises(ValueError, match="External"):
            capture(str(root), stage)


@pytest.mark.asyncio
async def test_storage_inside_workspace_cannot_capture_itself(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        await WorkspaceSnapshotTransfer().capture(WorkspaceRef(locator=str(tmp_path)), LocalProjectStorage(tmp_path / "storage"), "1")


@pytest.mark.asyncio
async def test_git_worktree_checkpoint_is_self_contained(store, tmp_path):
    if not shutil.which("git"):
        pytest.skip("Git is required.")
    repository = tmp_path / "repository"
    repository.mkdir()
    def git(*args, cwd=repository):
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()
    git("init")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    (repository / "tracked.txt").write_text("base")
    git("add", ".")
    git("commit", "-m", "Initial")
    git("remote", "add", "origin", "https://x-access-token:ephemeral@github.com/example/repo.git")
    worktree = tmp_path / "task"
    git("worktree", "add", "-b", "task-branch", str(worktree))
    (worktree / "tracked.txt").write_text("staged")
    git("add", "tracked.txt", cwd=worktree)
    (worktree / "tracked.txt").write_text("unstaged")
    (worktree / "untracked.txt").write_text("new")
    transfer = WorkspaceSnapshotTransfer()
    revision = store.put_manifest("1", await transfer.capture(WorkspaceRef(locator=str(worktree)), store, "1"))
    restored = tmp_path / "restored"
    transfer.materialize(store, "1", revision, restored)
    assert (restored / ".git").is_dir()
    assert "ephemeral" not in (restored / ".git/config").read_text()
    assert git("branch", "--show-current", cwd=restored) == "task-branch"
    assert "tracked.txt" in git("diff", "--cached", "--name-only", cwd=restored)
    assert "tracked.txt" in git("diff", "--name-only", cwd=restored)
    assert (restored / "untracked.txt").read_text() == "new"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["daytona", "modal"])
async def test_cloud_transfer_uses_bounded_binary_provider_primitives(provider, store, tmp_path, monkeypatch):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    payload = bytes(range(256)) * (CHUNK_BYTES // 256 + 7)
    (source / "binary.dat").write_bytes(payload)
    uploaded_sizes = []
    captured_objects = []
    from agent.modules.projects import snapshot_worker
    class Backend:
        def __init__(self, root):
            self.root = root.as_posix()
        def path(self, remote):
            relative = PurePosixPath(remote).relative_to("/tmp")
            return tmp_path / "transfers" / Path(*relative.parts)
        async def upload_coding_file(self, content, path):
            uploaded_sizes.append(len(content))
            target = self.path(path)
            assert target.parent.is_dir()
            target.write_bytes(content)
        async def download_coding_file(self, path):
            return self.path(path).read_bytes()
        async def execute_coding_command(self, command, **kwargs):
            args = shlex.split(command)
            if args[0] == "mkdir":
                self.path(args[-1]).mkdir(parents=True, exist_ok=True)
            else:
                _, worker, operation, root, stage = args
                if operation == "capture":
                    snapshot_worker.capture(root, str(self.path(stage)))
                    captured_objects.append(len(list((self.path(stage) / "objects").iterdir())))
                elif operation == "restore":
                    snapshot_worker.restore(root, str(self.path(stage)))
            return SimpleNamespace(exit_code=0, output="")
    transfer = WorkspaceSnapshotTransfer()
    async def backend(ref):
        return Backend(source if ref.locator == "source" else destination)
    monkeypatch.setattr(transfer, "_backend", backend)
    manifest = await transfer.capture(WorkspaceRef(backend=provider, locator="source"), store, "1")
    revision = store.put_manifest("1", manifest)
    assert await transfer.capture(WorkspaceRef(backend=provider, locator="source"), store, "1") == manifest
    assert captured_objects[0] == 2 and captured_objects[1] == 0
    await transfer.restore(WorkspaceRef(backend=provider, locator="destination"), store, "1", revision)
    assert (destination / "binary.dat").read_bytes() == payload
    assert max(uploaded_sizes) <= CHUNK_BYTES


@pytest.mark.asyncio
async def test_incremental_capture_reads_only_changed_files_and_repairs_missing_objects(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    (root / "stable.txt").write_bytes(b"stable")
    (root / "changed.txt").write_bytes(b"before")
    store = LocalProjectStorage(tmp_path / "storage")
    transfer = WorkspaceSnapshotTransfer()
    ref = WorkspaceRef(locator=str(root))
    first = await transfer.capture(ref, store, "1")
    store.put_manifest("1", first)
    reads = []
    original_open = Path.open
    def track_open(path, mode="r", *args, **kwargs):
        if mode == "rb" and (path.parent == root or (path.is_relative_to(store.root) and path.parent.name == "objects")):
            reads.append(path)
        return original_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", track_open)
    unchanged = await transfer.capture(ref, store, "1")
    store.put_manifest("1", unchanged)
    assert unchanged == first and reads == []

    (root / "changed.txt").write_bytes(b"after")
    changed = await transfer.capture(ref, store, "1")
    store.put_manifest("1", changed)
    assert reads == [root / "changed.txt"]
    reads.clear()
    digest = first["entries"]["stable.txt"]["checksum"]
    (store.root / "1/objects" / digest).write_bytes(b"broken")
    repaired = await transfer.capture(ref, store, "1")
    store.put_manifest("1", repaired)
    assert root / "stable.txt" in reads and root / "changed.txt" not in reads
    assert store.get_blob("1", digest) == b"stable"


def test_cached_publication_rechecks_modified_objects_and_file_checksums(tmp_path):
    store = LocalProjectStorage(tmp_path)
    store.put_blob("1", checksum(b"first"), b"first")
    store.put_blob("1", checksum(b"second"), b"second")
    entry = {"type": "file", "size": 11, "mode": 0o644, "checksum": checksum(b"firstsecond"),
             "chunks": [{"checksum": checksum(value), "size": len(value)} for value in (b"first", b"second")]}
    manifest = {"version": 1, "entries": {"file": entry}}
    store.put_manifest("1", manifest)
    invalid = {"version": 1, "entries": {"file": dict(entry, checksum=checksum(b"secondfirst"))}}
    with pytest.raises(ValueError, match="checksum"):
        store.put_manifest("1", invalid)
    (tmp_path / "1/objects" / checksum(b"first")).write_bytes(b"other")
    with pytest.raises(ValueError, match="checksum"):
        store.put_manifest("1", manifest)


@pytest.mark.asyncio
async def test_capture_preserves_bare_repository_configuration(store, tmp_path):
    root = tmp_path / "source"
    config = root / "vendor/repo.git/config"
    config.parent.mkdir(parents=True)
    original = b"[core]\n\tbare = true\n\tworktree = /custom/path\n[remote \"origin\"]\n\turl = https://x-access-token:secret@github.com/team/repo.git\n"
    config.write_bytes(original)
    transfer = WorkspaceSnapshotTransfer()
    revision = store.put_manifest("1", await transfer.capture(WorkspaceRef(locator=str(root)), store, "1"))
    destination = tmp_path / "restored"
    transfer.materialize(store, "1", revision, destination)
    restored = (destination / "vendor/repo.git/config").read_bytes()
    assert b"bare = true" in restored and b"worktree = /custom/path" in restored
    assert b"secret" not in restored
