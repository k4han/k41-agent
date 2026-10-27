"""Three-way application of a workspace revision to its original local directory."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from uuid import uuid4

from sqlalchemy import update

from agent.modules.projects import snapshot_worker
from agent.modules.projects.models import ProjectWorkspace
from agent.modules.projects.workspaces import ProjectWorkspaceError, _session, source_key
from agent.modules.workspaces import WorkspaceRef
from agent.modules.workspaces.policies import MANUAL_APPLY, MIRROR_STRICT, sync_policy

logger = logging.getLogger(__name__)
_WINDOWS = os.name == "nt"
_TEMPORARY_PREFIX = ".k41-sync-"


def local_link_summary(workspace, runtime) -> dict | None:
    source = WorkspaceRef.model_validate_json(workspace.source_json)
    if workspace.kind != "shared" or source.backend != "local":
        return None
    execution = WorkspaceRef.model_validate_json(runtime.execution_json)
    active = execution.backend == "local" and source_key(source) == source_key(execution)
    revision = workspace.local_sync_revision
    return {"path": source.locator, "revision": revision,
            "status": "active" if active else "baseline_missing" if not revision else
                      "applied" if revision == workspace.head_revision else "pending"}


def _capture(path: Path) -> tuple[dict, dict]:
    if (path / ".git").is_file():
        raise ProjectWorkspaceError("Linked Git worktrees require a Git-aware merge; automatic local sync is unavailable.", "local_sync_unsupported")
    with tempfile.TemporaryDirectory(prefix="k41-local-view-") as stage:
        manifest = snapshot_worker.capture(str(path), stage)
        state = json.loads((Path(stage) / "capture-state.json").read_text(encoding="utf-8"))
        return manifest, state["signatures"]


def _same_entry(left: dict | None, right: dict | None) -> bool:
    if left is None or right is None:
        return left == right

    def comparable(entry):
        if _WINDOWS:
            # Windows exposes the file's read-only flag, but no POSIX directory or execute permissions.
            mode = entry["mode"] & 0o200 if entry["type"] == "file" else 0
        else:
            # Only the permission bits an apply writes are comparable; setuid, setgid, and sticky bits are never applied.
            mode = entry["mode"] & 0o777
        return dict(entry, mode=mode)
    return comparable(left) == comparable(right)


def _verify(base: dict, desired: dict, current: dict) -> None:
    for name in base["entries"].keys() | desired["entries"].keys():
        old, new, actual = base["entries"].get(name), desired["entries"].get(name), current["entries"].get(name)
        if not _same_entry(old, new) and not _same_entry(actual, new):
            raise ProjectWorkspaceError("Local files changed during synchronization. Preview the changes again.", "stale_content")


def _plan(base: dict, desired: dict, current: dict, root: Path) -> tuple[list[dict], list[str]]:
    before, after, local = base["entries"], desired["entries"], current["entries"]
    changes, conflicts = [], set()
    for name in sorted(before.keys() | after.keys() | local.keys()):
        old, new, actual = before.get(name), after.get(name), local.get(name)
        if _same_entry(old, new) or _same_entry(actual, new):
            continue
        if not _same_entry(actual, old):
            conflicts.add(name)
            continue
        changes.append({"path": name, "action": "delete" if new is None else "add" if actual is None else "update"})
    directories = {change["path"] for change in changes if after.get(change["path"], {}).get("type") == "directory"}
    for change in changes:
        if change["path"] not in after:
            continue
        for parent in PurePosixPath(change["path"]).parents:
            name = str(parent)
            if name != "." and local.get(name, {}).get("type") != "directory" and name not in directories:
                conflicts.add(name)
    removals = {change["path"] for change in changes
                if local.get(change["path"]) and (after.get(change["path"]) is None or
                   after[change["path"]]["type"] != local[change["path"]]["type"])}
    for name in removals:
        if local[name]["type"] == "directory":
            for child in (root / name).iterdir():
                if child.relative_to(root).as_posix() not in removals:
                    conflicts.add(name)
    return changes, sorted(conflicts)


def plan_changes(base, desired, current, root, policy=MANUAL_APPLY):
    changes, conflicts = _plan(base, desired, current, root)
    if policy == MIRROR_STRICT:
        conflicts = set(conflicts)
        for name in base["entries"].keys() | current["entries"].keys():
            actual = current["entries"].get(name)
            if not _same_entry(actual, base["entries"].get(name)) and not _same_entry(actual, desired["entries"].get(name)):
                conflicts.add(name)
    return changes, sorted(conflicts)


async def apply_workspace_revision(transfer, store, key, revision, baseline, root, generation, *, preview_token=None,
                                   policy=MANUAL_APPLY, journal=None):
    """Preview or apply a pinned revision without changing its runtime or baseline."""
    from agent.modules.workspaces.host_mirror import validate_host_names
    root = Path(root)
    desired = await asyncio.to_thread(store.get_manifest, key, revision)
    validate_host_names(desired)
    base = await asyncio.to_thread(store.get_manifest, key, baseline) if baseline else {"version": 1, "entries": {}}
    if root.is_symlink() or root.is_junction():
        raise ProjectWorkspaceError("Local workspace root must not be a link.", "stale_content")
    if not root.exists() and baseline:
        raise ProjectWorkspaceError("The linked local directory is unavailable.", "local_sync_unsupported")
    journal = Path(journal) if journal is not None else Path(store.root) / key / "local-sync.json"
    if root.exists():
        await _run_writer(_recover_replacement, root, journal)
        await _run_writer(_sweep_temporary, root, base["entries"].keys() | desired["entries"].keys())
        current, signatures = await asyncio.to_thread(_capture, root)
    else:
        current, signatures = {"version": 1, "entries": {}}, {}
    changes, conflicts = await asyncio.to_thread(plan_changes, base, desired, current, root, policy)
    identity = {"generation": generation, "revision": revision, "base": baseline,
                "current": current, "path": str(root.resolve()), "policy": policy}
    token = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    result = {"path": str(root), "revision": revision, "preview_token": token,
              "changes": changes, "conflicts": conflicts, "applied": False}
    if preview_token is None:
        return result
    if preview_token != token:
        raise ProjectWorkspaceError("Workspace or local files changed. Preview the changes again.", "stale_content")
    if conflicts:
        raise ProjectWorkspaceError("Resolve local file conflicts before applying changes.", "local_sync_conflict")

    def apply():
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="k41-local-apply-") as stage:
            transfer.materialize(store, key, revision, Path(stage))
            _apply(root, Path(stage), desired, current, signatures, changes, journal=journal)
            verified, _ = _capture(root)
            _verify(base, desired, verified)
    await _run_writer(apply)
    merged = await transfer.capture(WorkspaceRef(locator=str(root)), store, key)
    merged_revision = await asyncio.to_thread(store.put_manifest, key, merged)
    return dict(result, applied=True, merged_revision=merged_revision)


def _target(root: Path, name: str) -> Path:
    path = root.joinpath(*PurePosixPath(name).parts)
    if root.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent == root or root in parent.parents):
        raise ProjectWorkspaceError("A linked directory path changed to a symlink. Preview the changes again.", "stale_content")
    return path


def _check(path: Path, expected: dict | None, signature: list | None) -> None:
    exists = path.exists() or path.is_symlink()
    if expected is None:
        valid = not exists
    elif not exists:
        valid = False
    elif expected["type"] == "directory":
        info = path.lstat()
        valid = path.is_dir() and not path.is_symlink() and signature is not None and [info.st_ino, info.st_dev] == signature[4:6]
    else:
        valid = list(snapshot_worker._signature(path)) == signature
    if not valid:
        raise ProjectWorkspaceError("A linked local file changed. Preview the changes again: " + str(path), "stale_content")


@contextmanager
def _writable_file(path: Path):
    """Restore read-only attributes if removing or replacing a Windows file fails."""
    original = path.lstat() if _WINDOWS else None
    readonly = original is not None and stat.S_ISREG(original.st_mode) and not original.st_mode & stat.S_IWRITE
    if readonly:
        path.chmod(stat.S_IMODE(original.st_mode) | stat.S_IWRITE)
    try:
        yield
    except BaseException:
        if readonly:
            try:
                current = path.lstat()
            except FileNotFoundError:
                pass
            else:
                if os.path.samestat(original, current):
                    path.chmod(stat.S_IMODE(original.st_mode))
        raise


def _write_replacement(journal: Path, state: dict) -> None:
    journal.parent.mkdir(parents=True, exist_ok=True)
    temporary = journal.with_name(".tmp-" + uuid4().hex)
    try:
        with temporary.open("xb") as handle:
            handle.write(json.dumps(state).encode())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, journal)
    finally:
        temporary.unlink(missing_ok=True)


def _temporary(name: str) -> bool:
    """Return whether a name is a replacement token this module could have created."""
    return (name.startswith(_TEMPORARY_PREFIX) and len(name) == len(_TEMPORARY_PREFIX) + 32
            and all(char in "0123456789abcdef" for char in name[len(_TEMPORARY_PREFIX):]))


def _discard(path: Path) -> None:
    """Remove a temporary entry without following symlinks or keeping read-only files."""
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_dir():
        with _writable_file(path):
            path.unlink()
        return
    for directory, _, _ in os.walk(path, followlinks=False):
        entry = Path(directory)
        entry.chmod(entry.stat().st_mode | 0o700)

    def retry_remove(function, filename, error):
        entry = Path(filename)
        if os.name != "nt" or entry.is_symlink():
            raise error
        entry.chmod(0o700)
        function(filename)
    shutil.rmtree(path, onexc=retry_remove)


def _sweep_temporary(root: Path, protected: set[str]) -> None:
    """Discard leftovers of an interrupted replacement that never reached the journal."""
    if root.is_symlink() or not root.is_dir():
        return
    for directory, directories, files in os.walk(root, followlinks=False):
        entry = Path(directory)
        for name in [*directories, *files]:
            candidate = entry / name
            if not _temporary(name) or candidate.relative_to(root).as_posix() in protected:
                continue
            try:
                _discard(candidate)
            except OSError as error:
                logger.warning("Could not discard local sync leftover %s: %s", candidate, error)
            else:
                if name in directories:
                    directories.remove(name)


def _settle_replacement(root: Path, state: dict) -> None:
    """Restore an unpublished original, or discard the backup after publication."""
    name, token = state["path"], state["temporary"]
    relative = PurePosixPath(name)
    if (state["root"] != str(root) or relative.is_absolute() or ".." in relative.parts or str(relative) != name
            or name in {"", "."} or "\\" in name or ":" in name or not _temporary(token)):
        raise ProjectWorkspaceError("Invalid local replacement recovery record.", "local_sync_conflict")
    path = _target(root, name)
    temporary = _target(root, (relative.parent / token).as_posix())
    if temporary.is_symlink() or not temporary.resolve().is_relative_to(root.resolve()):
        raise ProjectWorkspaceError("Local replacement recovery directory changed.", "local_sync_conflict")
    backup, prepared = temporary / "original", temporary / "replacement"
    # Publication moves the prepared entry out; its presence means the original still needs protection.
    if backup.exists() or backup.is_symlink():
        if not path.exists() and not path.is_symlink():
            backup.rename(path)
        elif prepared.exists() or prepared.is_symlink():
            raise ProjectWorkspaceError("A local replacement path was changed externally. Preserve or move it before retrying: " + str(path), "local_sync_conflict")
    # Only the validated operation directory is discarded; symlink targets stay untouched.
    _discard(temporary)


def _recover_replacement(root: Path, journal: Path) -> None:
    if journal.exists():
        _settle_replacement(root, json.loads(journal.read_text(encoding="utf-8")))
        journal.unlink()


async def _run_writer(function, *args) -> None:
    # A filesystem worker cannot be cancelled safely. Retain the reservation until it settles.
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise


def _replace_entry(root: Path, staged: Path, name: str, desired: dict, current: dict, signatures: dict,
                   journal: Path | None) -> None:
    """Prepare the entire replacement before moving the original into its recorded backup."""
    state = {"root": str(root), "path": name, "temporary": _TEMPORARY_PREFIX + uuid4().hex}
    path = _target(root, name)
    temporary = path.with_name(state["temporary"])
    if journal is not None:
        _write_replacement(journal, state)
    try:
        temporary.mkdir()
        prepared = temporary / "replacement"
        entry = desired["entries"][name]
        if entry["type"] == "directory":
            shutil.copytree(staged / name, prepared, symlinks=True)
        elif entry["type"] == "symlink":
            prepared.symlink_to(entry["target"], target_is_directory=(staged / name).is_dir())
        else:
            shutil.copyfile(staged / name, prepared, follow_symlinks=False)
            prepared.chmod(entry["mode"] & 0o777)
        for child in current["entries"]:
            if child == name or child.startswith(name + "/"):
                target = _target(root, child)
                _check(target, current["entries"][child], signatures.get(child))
                if list(snapshot_worker._signature(target)) != signatures.get(child):
                    raise ProjectWorkspaceError("Local files changed before replacement. Preview the changes again.", "stale_content")
        path.rename(temporary / "original")
        _check(path, None, None)
        prepared.rename(path)
    finally:
        _settle_replacement(root, state)
        if journal is not None:
            journal.unlink(missing_ok=True)


def _apply(root: Path, staged: Path, desired: dict, current: dict, signatures: dict, changes: list[dict],
           *, journal: Path | None = None) -> None:
    before, after = current["entries"], desired["entries"]
    names = [change["path"] for change in changes]
    replaced = {name for name in names if before.get(name) and after.get(name)
                and before[name]["type"] != after[name]["type"]}
    case_deletions = {os.path.normcase(name): name for name in names
                      if name in before and name not in after} if _WINDOWS else {}
    # Check the complete plan before the first write; per-file checks catch later external edits.
    for name in names:
        if name not in before and any(str(parent) in replaced for parent in PurePosixPath(name).parents):
            continue
        # A case-only rename still occupies its destination until the deletion phase.
        original = name if name in before else case_deletions.get(os.path.normcase(name), name)
        _check(_target(root, original), before.get(original), signatures.get(original))
    for name in sorted(replaced):
        _replace_entry(root, staged, name, desired, current, signatures, journal)
    names = [name for name in names if name not in replaced and not any(name.startswith(parent + "/") for parent in replaced)]
    removed = set()
    for name in sorted(names, key=lambda value: (-len(PurePosixPath(value).parts), value)):
        old, new = before.get(name), after.get(name)
        if old is None or (new is not None and old["type"] == new["type"]):
            continue
        path = _target(root, name)
        _check(path, old, signatures.get(name))
        if old["type"] == "directory":
            path.rmdir()
        else:
            with _writable_file(path):
                path.unlink()
        removed.add(name)
    for name in sorted(names, key=lambda value: (len(PurePosixPath(value).parts), value)):
        entry = after.get(name)
        if entry is None:
            continue
        path = _target(root, name)
        expected = None if name in removed else before.get(name)
        if entry["type"] == "directory":
            if expected is None:
                path.mkdir()
            continue
        _check(path, expected, signatures.get(name))
        temporary = path.with_name(_TEMPORARY_PREFIX + uuid4().hex)
        try:
            if entry["type"] == "symlink":
                temporary.symlink_to(entry["target"])
            else:
                shutil.copyfile(staged / name, temporary, follow_symlinks=False)
                temporary.chmod(entry["mode"] & 0o777)
            _check(path, expected, signatures.get(name))
            if expected is None:
                if _WINDOWS:
                    # Windows rename publishes atomically without overwriting an existing destination.
                    temporary.rename(path)
                else:
                    os.link(temporary, path, follow_symlinks=False)
            else:
                with _writable_file(path):
                    os.replace(temporary, path)
        finally:
            if temporary.exists() or temporary.is_symlink():
                with _writable_file(temporary):
                    temporary.unlink(missing_ok=True)
    for name in sorted(names, key=lambda value: -len(PurePosixPath(value).parts)):
        entry = after.get(name)
        if entry and entry["type"] == "directory":
            _target(root, name).chmod(entry["mode"] & 0o777)


class WorkspaceLocalSync:
    def __init__(self, service):
        self.service = service

    async def synchronize(self, project_id: int, expected_generation: int, workspace_id: int | None = None,
                          *, preview_token: str | None = None) -> dict:
        service = self.service
        project, workspace, runtime = await service.rows(project_id, workspace_id)
        supported = workspace.kind == "shared" or (workspace.kind == "github_task" and sync_policy(workspace) == MANUAL_APPLY)
        if service.closing or project.migration_status != "ready" or not supported or runtime.status not in {"ready", "unavailable"}:
            raise ProjectWorkspaceError("Recover the workspace before synchronizing local files.", "workspace_busy")
        link = local_link_summary(workspace, runtime)
        if link is None and sync_policy(workspace) == MANUAL_APPLY:
            link = {"path": str(service.host_mirrors.project_path(workspace, runtime)), "status": "pending"}
        if link is None or link["status"] == "active":
            raise ValueError("This workspace is not using a separate linked local directory.")
        if not workspace.local_sync_revision and WorkspaceRef.model_validate_json(workspace.source_json).backend == "local":
            raise ProjectWorkspaceError("No baseline is available for the linked directory. Existing local files have been preserved.", "local_sync_unsupported")
        await service.switcher.reserve_idle(workspace, runtime, expected_generation, allow_unavailable=runtime.status == "unavailable")
        task = asyncio.create_task(self._synchronize(project_id, workspace.id, expected_generation, link, preview_token))
        service.switcher.tasks[workspace.id] = task

        def completed(done):
            if not done.cancelled() and (error := done.exception()) is not None:
                logger.warning("Local sync needs a new preview workspace=%s: %s", workspace.id, error)

        task.add_done_callback(completed)
        return await asyncio.shield(task)

    async def _synchronize(self, project_id, workspace_id, expected_generation, link, preview_token):
        service = self.service
        owner = asyncio.current_task()
        try:
            async with service.lock(workspace_id):
                _, workspace, runtime = await service.rows(project_id, workspace_id)
                if runtime.generation != expected_generation:
                    raise ProjectWorkspaceError("Execution runtime changed; preview the local changes again.", "stale_runtime")
                # A preview must not apply files as a side effect of capturing the sandbox.
                saved = ({"revision": workspace.head_revision} if runtime.status == "unavailable" else
                         await service._checkpoint(project_id, workspace.id, expected_generation, sync_host=False))
                if not saved["revision"]:
                    raise ProjectWorkspaceError("No saved files are available.", "missing_data")
                result = await apply_workspace_revision(service.transfer, service.storage, str(workspace.id),
                    saved["revision"], workspace.local_sync_revision, Path(link["path"]), expected_generation,
                    preview_token=preview_token, policy=MANUAL_APPLY,
                    journal=service.runtime_root / str(workspace.id) / "local-sync.json")
                if not result["applied"]:
                    return result
                session = await _session()
                async with session:
                    state = json.loads(workspace.host_mirror_json or "{}")
                    state.update(path=link["path"], revision=saved["revision"], remote_revision=saved["revision"],
                                 status="synced", generation=expected_generation, conflicts=[], last_error="")
                    await session.execute(update(ProjectWorkspace).where(ProjectWorkspace.id == workspace.id).values(
                        local_sync_revision=saved["revision"], host_mirror_json=json.dumps(state)))
                    await session.commit()
                service._notify_runtime_changed(project_id, workspace.id)
                return result
        finally:
            if service.switcher.tasks.get(workspace_id) is owner:
                service.switcher.tasks.pop(workspace_id, None)
            service.switcher._release(workspace_id)
