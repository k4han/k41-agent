"""Local synchronization keeps independent edits and rejects unsafe filesystem changes."""

import copy
import os
from pathlib import Path
import shutil
import stat

import pytest

from agent.modules.projects import local_sync
from agent.modules.projects.local_sync import (
    _apply,
    _capture,
    _plan,
    _recover_replacement,
    _same_entry,
    _sweep_temporary,
    _verify,
)
from agent.modules.projects.workspaces import ProjectWorkspaceError


@pytest.mark.parametrize("operation", ["file_to_directory", "directory_to_file", "delete_directory"])
def test_local_sync_applies_type_changes_and_deletions_without_removing_unrelated_files(tmp_path, operation):
    root, source = tmp_path / "linked", tmp_path / "source"
    root.mkdir()
    if operation == "file_to_directory":
        (root / "entry").write_text("old")
    else:
        (root / "entry").mkdir()
        (root / "entry" / "child.txt").write_text("old")
    base, _ = _capture(root)
    shutil.copytree(root, source)
    (root / "unrelated.txt").write_text("independent local file")
    if operation == "file_to_directory":
        (source / "entry").unlink()
        (source / "entry").mkdir()
        (source / "entry" / "child.txt").write_text("new")
    else:
        shutil.rmtree(source / "entry")
        if operation == "directory_to_file":
            (source / "entry").write_text("new")
    desired, _ = _capture(source)
    current, signatures = _capture(root)
    changes, conflicts = _plan(base, desired, current, root)
    assert not conflicts
    _apply(root, source, desired, current, signatures, changes)
    actual, _ = _capture(root)
    assert all(actual["entries"].get(name) == desired["entries"].get(name)
               for name in base["entries"].keys() | desired["entries"].keys())
    assert (root / "unrelated.txt").read_text() == "independent local file"


def test_local_sync_reports_conflicts_for_new_children_in_a_deleted_directory(tmp_path):
    root, source = tmp_path / "linked", tmp_path / "source"
    (root / "entry").mkdir(parents=True)
    (root / "entry" / "old.txt").write_text("old")
    base, _ = _capture(root)
    source.mkdir()
    desired, _ = _capture(source)
    (root / "entry" / "new.txt").write_text("keep")
    current, _ = _capture(root)
    _, conflicts = _plan(base, desired, current, root)
    assert "entry" in conflicts
    assert (root / "entry" / "new.txt").read_text() == "keep"


def test_local_sync_rechecks_all_target_files_before_any_write(tmp_path):
    root, source = tmp_path / "linked", tmp_path / "source"
    root.mkdir()
    (root / "file.txt").write_text("original")
    base, _ = _capture(root)
    shutil.copytree(root, source)
    (source / "file.txt").write_text("remote edit")
    (source / "a.txt").write_text("new")
    desired, _ = _capture(source)
    current, signatures = _capture(root)
    changes, conflicts = _plan(base, desired, current, root)
    assert not conflicts
    (root / "file.txt").write_text("external edit")
    with pytest.raises(ProjectWorkspaceError) as failure:
        _apply(root, source, desired, current, signatures, changes)
    assert failure.value.code == "stale_content"
    assert not (root / "a.txt").exists()
    assert (root / "file.txt").read_text() == "external edit"


def test_local_sync_replaces_internal_symlink_without_changing_its_old_target(tmp_path):
    root, source = tmp_path / "linked", tmp_path / "source"
    (root / "original").mkdir(parents=True)
    (root / "original" / "keep.txt").write_text("keep")
    try:
        (root / "entry").symlink_to("original", target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable")
    base, _ = _capture(root)
    source.mkdir()
    shutil.copytree(root / "original", source / "original")
    (source / "entry").mkdir()
    (source / "entry" / "new.txt").write_text("new")
    desired, _ = _capture(source)
    current, signatures = _capture(root)
    changes, conflicts = _plan(base, desired, current, root)
    assert not conflicts
    _apply(root, source, desired, current, signatures, changes)
    assert not (root / "entry").is_symlink()
    assert (root / "entry" / "new.txt").read_text() == "new"
    assert (root / "original" / "keep.txt").read_text() == "keep"


@pytest.mark.parametrize("existing", [False, True])
def test_local_sync_applies_posix_modes_on_windows_and_recognizes_applied_files(tmp_path, monkeypatch, existing):
    monkeypatch.setattr(local_sync, "_WINDOWS", True)
    root, source = tmp_path / "linked", tmp_path / "source"
    root.mkdir()
    if existing:
        (root / "folder").mkdir()
        (root / "folder" / "file.txt").write_text("old")
    base, _ = _capture(root)
    shutil.copytree(root, source)
    (source / "folder").mkdir(exist_ok=True)
    (source / "folder" / "file.txt").write_text("cloud edit")
    desired, _ = _capture(source)
    desired["entries"]["folder"]["mode"] = 0o755
    desired["entries"]["folder/file.txt"]["mode"] = 0o644
    current, signatures = _capture(root)
    changes, conflicts = _plan(base, desired, current, root)
    assert not conflicts
    _apply(root, source, desired, current, signatures, changes)
    actual, _ = _capture(root)
    _verify(base, desired, actual)
    assert _plan(base, desired, actual, root) == ([], [])
    assert (root / "folder" / "file.txt").read_text() == "cloud edit"


@pytest.mark.parametrize("windows", [True, False])
def test_local_sync_retains_supported_permission_changes(tmp_path, monkeypatch, windows):
    monkeypatch.setattr(local_sync, "_WINDOWS", windows)
    (tmp_path / "script").write_text("script")
    base, _ = _capture(tmp_path)
    base["entries"]["script"]["mode"] = 0o644
    desired = copy.deepcopy(base)
    desired["entries"]["script"]["mode"] = 0o755
    changes, conflicts = _plan(base, desired, base, tmp_path)
    assert not conflicts
    assert bool(changes) is not windows
    desired["entries"]["script"]["mode"] = 0o444
    assert _plan(base, desired, base, tmp_path)[0] == [{"path": "script", "action": "update"}]


@pytest.mark.parametrize("operation", ["add", "update", "make_writable", "delete"])
def test_local_sync_handles_readonly_files_without_leaving_temporary_links(tmp_path, monkeypatch, operation):
    monkeypatch.setattr(local_sync, "_WINDOWS", True)
    root, source = tmp_path / "linked", tmp_path / "source"
    root.mkdir()
    source.mkdir()
    if operation != "add":
        (root / "file.txt").write_text("original")
        (root / "file.txt").chmod(0o444)
    base, signatures = _capture(root)
    if operation != "delete":
        (source / "file.txt").write_text("cloud edit")
        (source / "file.txt").chmod(0o644 if operation == "make_writable" else 0o444)
    desired, _ = _capture(source)
    changes, conflicts = _plan(base, desired, base, root)
    assert not conflicts
    _apply(root, source, desired, base, signatures, changes)
    actual, _ = _capture(root)
    _verify(base, desired, actual)
    assert _plan(base, desired, actual, root) == ([], [])
    assert not list(root.glob(".k41-sync-*"))
    if operation == "delete":
        assert not (root / "file.txt").exists()
    else:
        assert (root / "file.txt").read_text() == "cloud edit"
        assert bool((root / "file.txt").stat().st_mode & stat.S_IWRITE) is (operation == "make_writable")


@pytest.mark.parametrize("operation,mode", [("update", 0o444), ("update", 0o644), ("delete", None)])
def test_local_sync_restores_readonly_flag_after_failed_write(tmp_path, monkeypatch, operation, mode):
    monkeypatch.setattr(local_sync, "_WINDOWS", True)
    root, source = tmp_path / "linked", tmp_path / "source"
    root.mkdir()
    source.mkdir()
    path = root / "file.txt"
    path.write_text("original")
    path.chmod(0o444)
    base, signatures = _capture(root)
    if operation == "update":
        (source / "file.txt").write_text("cloud edit")
        (source / "file.txt").chmod(mode)
    desired, _ = _capture(source)
    changes, conflicts = _plan(base, desired, base, root)
    assert not conflicts
    original_unlink = Path.unlink
    def fail_replace(*args, **kwargs):
        raise OSError("disk full")
    def fail_unlink(target, *args, **kwargs):
        if target == path:
            raise OSError("disk full")
        return original_unlink(target, *args, **kwargs)
    with monkeypatch.context() as patch:
        if operation == "update":
            patch.setattr(os, "replace", fail_replace)
        else:
            patch.setattr(Path, "unlink", fail_unlink)
        with pytest.raises(OSError, match="disk full"):
            _apply(root, source, desired, base, signatures, changes)
    assert path.read_text() == "original"
    assert not path.stat().st_mode & stat.S_IWRITE
    assert not list(root.glob(".k41-sync-*"))


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows rename semantics")
@pytest.mark.parametrize("readonly", [False, True])
def test_local_sync_new_file_publish_preserves_external_file(tmp_path, monkeypatch, readonly):
    root, source = tmp_path / "linked", tmp_path / "source"
    root.mkdir()
    source.mkdir()
    base, signatures = _capture(root)
    (source / "file.txt").write_text("cloud addition")
    (source / "file.txt").chmod(0o444 if readonly else 0o644)
    desired, _ = _capture(source)
    changes, conflicts = _plan(base, desired, base, root)
    assert not conflicts
    original_rename = Path.rename
    def external_write(path, destination):
        if path.name.startswith(".k41-sync-"):
            Path(destination).write_text("external edit")
        return original_rename(path, destination)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", external_write)
        with pytest.raises(FileExistsError):
            _apply(root, source, desired, base, signatures, changes)
    assert (root / "file.txt").read_text() == "external edit"
    assert not list(root.glob(".k41-sync-*"))


@pytest.mark.skipif(os.name != "nt", reason="Requires a case-insensitive Windows filesystem")
@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("edited", [False, True])
@pytest.mark.parametrize("readonly", [False, True])
def test_local_sync_applies_case_only_renames(tmp_path, directory, edited, readonly):
    root, source = tmp_path / "linked", tmp_path / "source"
    root.mkdir()
    original = root / "Folder" / "README.md" if directory else root / "README.md"
    original.parent.mkdir(exist_ok=True)
    original.write_text("original")
    if readonly:
        original.chmod(0o444)
    (root / "unrelated.txt").write_text("keep")
    base, signatures = _capture(root)
    shutil.copytree(root, source)
    (source / ("Folder" if directory else "README.md")).rename(source / ("folder" if directory else "readme.md"))
    target = source / "folder" / "README.md" if directory else source / "readme.md"
    if edited:
        target.chmod(0o644)
        target.write_text("cloud edit")
        if readonly:
            target.chmod(0o444)
    desired, _ = _capture(source)
    changes, conflicts = _plan(base, desired, base, root)
    assert not conflicts
    _apply(root, source, desired, base, signatures, changes)
    actual, _ = _capture(root)
    _verify(base, desired, actual)
    assert actual == desired
    assert (root / "unrelated.txt").read_text() == "keep"
    assert not list(root.rglob(".k41-sync-*"))


@pytest.mark.skipif(os.name != "nt", reason="Requires a case-insensitive Windows filesystem")
def test_local_sync_rechecks_case_only_rename_before_any_write(tmp_path):
    root, source = tmp_path / "linked", tmp_path / "source"
    root.mkdir()
    source.mkdir()
    (root / "README.md").write_text("original")
    base, signatures = _capture(root)
    (source / "readme.md").write_text("original")
    (source / "a.txt").write_text("cloud addition")
    desired, _ = _capture(source)
    changes, conflicts = _plan(base, desired, base, root)
    assert not conflicts
    (root / "README.md").write_text("external edit")
    with pytest.raises(ProjectWorkspaceError) as failure:
        _apply(root, source, desired, base, signatures, changes)
    assert failure.value.code == "stale_content"
    assert not (root / "a.txt").exists()
    assert (root / "README.md").read_text() == "external edit"


@pytest.mark.parametrize("replacement", [None, "file"])
def test_local_sync_reports_missing_or_replaced_parent_before_applying_any_files(tmp_path, replacement):
    root, source = tmp_path / "linked", tmp_path / "source"
    (root / "folder").mkdir(parents=True)
    (root / "folder" / "old.txt").write_text("old")
    (root / "a.txt").write_text("original")
    base, _ = _capture(root)
    shutil.copytree(root, source)
    (source / "folder" / "new.txt").write_text("cloud addition")
    (source / "a.txt").write_text("cloud edit")
    desired, _ = _capture(source)
    shutil.rmtree(root / "folder")
    if replacement:
        (root / "folder").write_text("independent local file")
    current, _ = _capture(root)
    _, conflicts = _plan(base, desired, current, root)
    assert conflicts == ["folder"]
    assert (root / "a.txt").read_text() == "original"


def replacement_views(tmp_path, operation):
    root, source = tmp_path / "linked", tmp_path / "source"
    root.mkdir()
    if operation == "file_to_directory":
        (root / "entry").write_text("original")
        (source / "entry").mkdir(parents=True)
        (source / "entry" / "child.txt").write_text("new child")
    else:
        (root / "entry").mkdir()
        (root / "entry" / "child.txt").write_text("original child")
        source.mkdir()
        (source / "entry").write_text("replacement")
    base, signatures = _capture(root)
    desired, _ = _capture(source)
    changes, conflicts = _plan(base, desired, base, root)
    assert not conflicts
    return root, source, base, desired, signatures, changes


@pytest.mark.parametrize("operation", ["file_to_directory", "directory_to_file"])
@pytest.mark.parametrize("phase", ["prepare", "publish"])
def test_local_sync_restores_type_replacements_after_io_failure(tmp_path, monkeypatch, operation, phase):
    root, source, base, desired, signatures, changes = replacement_views(tmp_path, operation)
    journal = tmp_path / "managed" / "local-sync.json"
    original_rename = Path.rename
    def fail_publish(path, destination):
        if path.name == "replacement":
            raise OSError("disk full")
        return original_rename(path, destination)
    with monkeypatch.context() as patch:
        if phase == "prepare":
            def fail_copy(*args, **kwargs):
                raise OSError("disk full")
            patch.setattr(shutil, "copytree" if operation == "file_to_directory" else "copyfile", fail_copy)
        else:
            patch.setattr(Path, "rename", fail_publish)
        with pytest.raises(OSError, match="disk full"):
            _apply(root, source, desired, base, signatures, changes, journal=journal)
    actual, signatures = _capture(root)
    assert actual == base
    assert not journal.exists()
    changes, conflicts = _plan(base, desired, actual, root)
    assert not conflicts
    _apply(root, source, desired, actual, signatures, changes, journal=journal)
    actual, _ = _capture(root)
    _verify(base, desired, actual)
    assert not journal.exists()
    assert not list(root.glob(".k41-sync-*"))


@pytest.mark.parametrize("operation", ["file_to_directory", "directory_to_file"])
@pytest.mark.parametrize("published", [False, True])
def test_local_sync_recovers_interrupted_replacements_without_overwriting_published_files(tmp_path, monkeypatch, operation, published):
    root, source, base, desired, signatures, changes = replacement_views(tmp_path, operation)
    journal = tmp_path / "managed" / "local-sync.json"
    original_rename = Path.rename
    def fail_publish(path, destination):
        if path.name == "replacement":
            raise OSError("disk full")
        return original_rename(path, destination)
    def interrupted_cleanup(*args):
        raise RuntimeError("application interrupted")
    with monkeypatch.context() as patch:
        if not published:
            patch.setattr(Path, "rename", fail_publish)
        patch.setattr(local_sync, "_settle_replacement", interrupted_cleanup)
        with pytest.raises(RuntimeError, match="application interrupted"):
            _apply(root, source, desired, base, signatures, changes, journal=journal)
    assert journal.is_file()
    _recover_replacement(root, journal)
    actual, signatures = _capture(root)
    assert actual == (desired if published else base)
    changes, conflicts = _plan(base, desired, actual, root)
    assert not conflicts
    _apply(root, source, desired, actual, signatures, changes, journal=journal)
    actual, _ = _capture(root)
    _verify(base, desired, actual)
    assert not journal.exists()
    assert not list(root.glob(".k41-sync-*"))


def test_local_sync_preserves_external_files_when_replacement_recovery_conflicts(tmp_path, monkeypatch):
    root, source, base, desired, signatures, changes = replacement_views(tmp_path, "directory_to_file")
    journal = tmp_path / "managed" / "local-sync.json"
    original_rename = Path.rename
    def external_write(path, destination):
        if path.name == "replacement":
            Path(destination).write_text("external edit")
            raise FileExistsError("external file appeared")
        return original_rename(path, destination)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", external_write)
        with pytest.raises(ProjectWorkspaceError) as failure:
            _apply(root, source, desired, base, signatures, changes, journal=journal)
    assert failure.value.code == "local_sync_conflict"
    assert (root / "entry").read_text() == "external edit"
    assert journal.is_file()
    with pytest.raises(ProjectWorkspaceError):
        _recover_replacement(root, journal)
    (root / "entry").rename(root / "external.txt")
    _recover_replacement(root, journal)
    assert (root / "entry" / "child.txt").read_text() == "original child"
    actual, signatures = _capture(root)
    changes, conflicts = _plan(base, desired, actual, root)
    assert not conflicts
    _apply(root, source, desired, actual, signatures, changes, journal=journal)
    assert (root / "entry").read_text() == "replacement"
    assert (root / "external.txt").read_text() == "external edit"
    assert not journal.exists()


def test_local_sync_rechecks_directory_contents_after_preparing_replacement(tmp_path, monkeypatch):
    root, source, base, desired, signatures, changes = replacement_views(tmp_path, "directory_to_file")
    journal = tmp_path / "managed" / "local-sync.json"
    original_copy = shutil.copyfile
    def external_write(*args, **kwargs):
        result = original_copy(*args, **kwargs)
        (root / "entry" / "external.txt").write_text("external edit")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(shutil, "copyfile", external_write)
        with pytest.raises(ProjectWorkspaceError) as failure:
            _apply(root, source, desired, base, signatures, changes, journal=journal)
    assert failure.value.code == "stale_content"
    assert (root / "entry" / "child.txt").read_text() == "original child"
    assert (root / "entry" / "external.txt").read_text() == "external edit"
    assert not journal.exists()


def test_local_sync_compares_only_permission_bits(monkeypatch):
    monkeypatch.setattr(local_sync, "_WINDOWS", False)
    entry = {"type": "file", "mode": 0o644, "size": 1, "digest": "abc"}
    assert _same_entry(entry, dict(entry, mode=0o4644))
    assert _same_entry(entry, dict(entry, mode=0o2644))
    assert not _same_entry(entry, dict(entry, mode=0o600))


def test_local_sync_sweeps_interrupted_replacements_and_keeps_unrelated_names(tmp_path):
    root = tmp_path / "linked"
    root.mkdir()
    (root / "folder").mkdir()
    directory = root / (".k41-sync-" + "a" * 32)
    (directory / "original").mkdir(parents=True)
    (directory / "original" / "file.txt").write_text("backup")
    nested = root / "folder" / (".k41-sync-" + "b" * 32)
    nested.write_text("stale")
    tracked = root / "folder" / (".k41-sync-" + "c" * 32)
    tracked.write_text("tracked")
    unrelated = root / (".k41-sync-" + "z")
    unrelated.write_text("keep")
    _sweep_temporary(root, {"folder/" + tracked.name})
    assert not directory.exists()
    assert not nested.exists()
    assert tracked.read_text() == "tracked"
    assert unrelated.read_text() == "keep"


def test_local_sync_sweep_does_not_follow_symlinked_leftovers(tmp_path):
    root, outside = tmp_path / "linked", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    link = root / (".k41-sync-" + "d" * 32)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable")
    _sweep_temporary(root, set())
    assert not link.exists()
    assert (outside / "keep.txt").read_text() == "keep"
