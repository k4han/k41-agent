"""Regression coverage for installation, managed identities, and discovery."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

import agent.modules.skills.discovery as discovery
import agent.modules.skills.repository as repository_module
import agent.modules.workspaces as workspaces
from agent.modules.skills import reload_repository_skills
from agent.modules.skills.models import Skill
from agent.modules.skills.parser import parse_skill_md
from agent.modules.skills.rendering import skill_content_xml
from agent.modules.skills.resources import list_workspace_resources
from agent.modules.skills.repository import FilesystemSkillRepository
from agent.modules.skills.service import SkillsService
from agent.modules.workspaces import WorkspaceRef


def _content(name: str = "demo", body: str = "Original body.") -> str:
    return f"---\nname: {name}\ndescription: Test skill.\n---\n{body}\n"


def _create(parent: Path, directory: str = "demo", *, name: str = "demo", body: str = "Original body.") -> Path:
    path = parent / directory
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(_content(name, body), encoding="utf-8")
    resource = path / "scripts" / "run.py"
    resource.parent.mkdir()
    resource.write_text("print('original')\n", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _clear_discovery():
    discovery.clear_discovery_cache()
    yield
    discovery.clear_discovery_cache()


@pytest.fixture
def fake_workspaces(monkeypatch):
    state = SimpleNamespace(files={}, reads=[], trees=[], on_read=None)

    def key(ref):
        return ref.backend, ref.locator, ref.metadata.get("root", "")

    class Backend:
        def __init__(self, ref):
            self.ref = ref

        async def tree(self, path):
            state.trees.append((key(self.ref), path))
            entries = {}
            prefix = path.rstrip("/") + "/"
            for file in state.files.get(key(self.ref), {}):
                if not file.startswith(prefix):
                    continue
                relative = file[len(prefix):]
                name, separator, _ = relative.partition("/")
                entries[name] = {"name": name, "kind": "directory" if separator else "file"}
            return {"entries": list(entries.values())}

        async def read_text(self, path):
            state.reads.append((key(self.ref), path))
            snapshot = state.files[key(self.ref)][path]
            if state.on_read is not None:
                await state.on_read()
            return snapshot

    async def backend(ref, **kwargs):
        return Backend(ref)

    monkeypatch.setattr(workspaces, "get_workspace_browser", backend)
    monkeypatch.setattr(workspaces, "get_workspace_file_io", backend)
    state.key = key
    return state


def test_install_copy_failure_preserves_existing_skill(tmp_path, monkeypatch):
    source = _create(tmp_path / "source", body="Replacement.")
    root = tmp_path / "installed"
    previous = _create(root)
    repo = FilesystemSkillRepository(root)
    repo.load_skill("demo")

    def fail_copy(source, destination):
        destination.mkdir()
        (destination / "partial.txt").write_text("partial", encoding="utf-8")
        raise OSError("Copy failed")

    monkeypatch.setattr(repository_module.shutil, "copytree", fail_copy)
    with pytest.raises(OSError, match="Copy failed"):
        repo.install(source)
    assert repo.load_skill("demo").body == "Original body."
    assert (previous / "scripts" / "run.py").read_text(encoding="utf-8") == "print('original')\n"
    assert not list(root.glob(".install-*"))


def test_install_swap_failure_restores_existing_directory(tmp_path, monkeypatch):
    source = _create(tmp_path / "source", body="Replacement.")
    root = tmp_path / "installed"
    previous = _create(root)
    original_rename = Path.rename

    def fail_swap(path, target):
        if path.name == "payload":
            raise OSError("Swap failed")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_swap)
    with pytest.raises(OSError, match="Swap failed"):
        FilesystemSkillRepository(root).install(source)
    assert (previous / "SKILL.md").read_text(encoding="utf-8") == _content()
    assert (previous / "scripts" / "run.py").is_file()
    assert not list(root.glob(".install-*"))


def test_install_failed_restore_keeps_recoverable_backup(tmp_path, monkeypatch):
    source = _create(tmp_path / "source", body="Replacement.")
    root = tmp_path / "installed"
    _create(root)
    original_rename = Path.rename

    def fail_swap_and_restore(path, target):
        if path.name in {"payload", "backup"}:
            raise OSError("Rename failed")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_swap_and_restore)
    with pytest.raises(OSError, match="Rename failed"):
        FilesystemSkillRepository(root).install(source)
    backups = list(root.glob(".install-*/backup/SKILL.md"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == _content()


@pytest.mark.parametrize("relationship", ["same", "nested", "ancestor"])
def test_install_rejects_overlapping_source_without_deleting_files(tmp_path, relationship):
    root = tmp_path / "installed"
    destination = _create(root)
    if relationship == "same":
        source = destination
    elif relationship == "nested":
        source = _create(destination, "nested")
    else:
        source = root
        (source / "SKILL.md").write_text(_content(), encoding="utf-8")
    with pytest.raises(ValueError, match="overlap"):
        FilesystemSkillRepository(root).install(source)
    assert (destination / "SKILL.md").read_text(encoding="utf-8") == _content()
    assert (source / "SKILL.md").is_file()


def test_install_replaces_skill_and_resources_after_validation(tmp_path):
    source = _create(tmp_path / "source", "different-folder", body="Replacement.")
    (source / "scripts" / "run.py").unlink()
    (source / "scripts" / "new.py").write_text("print('new')\n", encoding="utf-8")
    root = tmp_path / "installed"
    _create(root)
    repo = FilesystemSkillRepository(root)
    installed = repo.install(source)
    assert installed.name == "demo"
    assert installed.path == root / "demo"
    assert installed.resources == ["scripts/new.py"]
    assert repo.load_skill("demo").body == "Replacement."
    assert not (root / "demo" / "scripts" / "run.py").exists()
    assert not list(root.glob(".install-*"))


@pytest.mark.parametrize("valid_skill", [True, False])
def test_install_rejects_destination_owned_by_another_skill(tmp_path, valid_skill):
    root = tmp_path / "installed"
    previous = _create(root, "demo", name="unrelated")
    if not valid_skill:
        (previous / "SKILL.md").write_text("Invalid skill content.\n", encoding="utf-8")
    original_content = (previous / "SKILL.md").read_text(encoding="utf-8")
    source = _create(tmp_path / "source", "bundle", body="Replacement.")
    repo = FilesystemSkillRepository(root)

    with pytest.raises(FileExistsError):
        repo.install(source)

    assert (previous / "SKILL.md").read_text(encoding="utf-8") == original_content
    assert (previous / "scripts" / "run.py").read_text(encoding="utf-8") == "print('original')\n"
    assert (source / "SKILL.md").read_text(encoding="utf-8") == _content(body="Replacement.")
    assert not list(root.glob(".install-*"))


def test_install_replaces_catalog_identity_without_touching_unrelated_directory(tmp_path):
    root = tmp_path / "installed"
    previous = _create(root, "imported-folder")
    unrelated = _create(root, "demo", name="unrelated")
    source = _create(tmp_path / "source", "bundle", body="Replacement.")
    repo = FilesystemSkillRepository(root)

    installed = repo.install(source)

    assert installed.path == previous
    assert repo.load_skill("demo").body == "Replacement."
    assert (unrelated / "SKILL.md").read_text(encoding="utf-8") == _content("unrelated")
    assert (unrelated / "scripts" / "run.py").is_file()
    assert not list(root.glob(".install-*"))


def test_managed_operations_follow_catalog_identity_instead_of_directory(tmp_path):
    alias = _create(tmp_path, "folder-name", name="catalog-name")
    unrelated = _create(tmp_path, "catalog-name", name="unrelated")
    repo = FilesystemSkillRepository(tmp_path)
    assert repo.read_skill_content("catalog-name") == _content("catalog-name")
    updated = repo.update_skill("catalog-name", "catalog-name", _content("catalog-name", "Updated."))
    assert updated.path == alias
    assert updated.resources == ["scripts/run.py"]
    assert repo.load_skill("catalog-name").body == "Updated."
    repo.delete_skill("catalog-name")
    assert not (alias / "SKILL.md").exists()
    assert (alias / "scripts" / "run.py").is_file()
    assert (unrelated / "SKILL.md").read_text(encoding="utf-8") == _content("unrelated")


@pytest.mark.parametrize("name_field", ["", "name: null\n", "name: ''\n"])
def test_update_imported_skill_rejects_content_that_changes_fallback_identity(tmp_path, name_field):
    directory = _create(tmp_path, "imported-folder")
    repo = FilesystemSkillRepository(tmp_path)
    content = f"---\n{name_field}description: Updated.\n---\nUpdated body.\n"

    with pytest.raises(ValueError, match="name must match"):
        repo.update_skill("demo", "demo", content)

    assert (directory / "SKILL.md").read_text(encoding="utf-8") == _content()
    assert repo.load_skill("demo").body == "Original body."
    assert repo.load_skill("imported-folder") is None


def test_install_without_name_preserves_existing_imported_identity_on_rejection(tmp_path):
    root = tmp_path / "installed"
    previous = _create(root, "imported-folder")
    source = _create(tmp_path / "source")
    content = "---\ndescription: Replacement.\n---\nReplacement body.\n"
    (source / "SKILL.md").write_text(content, encoding="utf-8")
    repo = FilesystemSkillRepository(root)

    with pytest.raises(ValueError, match="name must match"):
        repo.install(source)

    assert (previous / "SKILL.md").read_text(encoding="utf-8") == _content()
    assert repo.load_skill("demo").body == "Original body."
    assert not list(root.glob(".install-*"))


def test_managed_writes_without_name_use_actual_directory_identity(tmp_path):
    content = "---\ndescription: Test skill.\n---\nBody.\n"
    repo = FilesystemSkillRepository(tmp_path / "installed")
    created = repo.create_skill("demo", content)
    assert repo.load_skill(created.name).name == "demo"

    updated = repo.update_skill("demo", "renamed", content)
    assert repo.load_skill(updated.name).name == "renamed"
    assert repo.load_skill("demo") is None

    source = tmp_path / "source" / "renamed"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(content, encoding="utf-8")
    installed = repo.install(source)
    assert repo.load_skill(installed.name).name == "renamed"


@pytest.mark.parametrize("warm_cache", [False, True])
@pytest.mark.parametrize("content", [
    "---\nname: demo\n---\nMissing description.\n",
    "---\nname: demo\ndescription: [\n---\nMalformed YAML.\n",
    "No frontmatter.\n",
])
def test_invalid_managed_skill_can_be_read_repaired_and_deleted(tmp_path, content, warm_cache):
    directory = _create(tmp_path)
    repo = FilesystemSkillRepository(tmp_path)
    if warm_cache:
        assert repo.load_skill("demo") is not None
    skill_md = directory / "SKILL.md"
    skill_md.write_text(content, encoding="utf-8")

    assert repo.load_skill("demo") is None
    assert repo.read_skill_content("demo") == content
    repaired = repo.update_skill("demo", "demo", _content(body="Repaired."))
    assert repaired.path == directory
    assert repo.load_skill("demo").body == "Repaired."
    assert repaired.resources == ["scripts/run.py"]

    skill_md.write_text(content, encoding="utf-8")
    repo.delete_skill("demo")
    assert not skill_md.exists()
    assert (directory / "scripts" / "run.py").is_file()


def test_managed_directory_fallback_does_not_modify_another_valid_identity(tmp_path):
    directory = _create(tmp_path, "demo", name="unrelated")
    repo = FilesystemSkillRepository(tmp_path)

    for operation in (
        lambda: repo.read_skill_content("demo"),
        lambda: repo.update_skill("demo", "demo", _content()),
        lambda: repo.delete_skill("demo"),
    ):
        with pytest.raises(FileNotFoundError):
            operation()
        assert (directory / "SKILL.md").read_text(encoding="utf-8") == _content("unrelated")


def test_invalid_skill_fallback_rejects_path_traversal(tmp_path):
    outside = _create(tmp_path, "outside")
    content = "Invalid skill.\n"
    (outside / "SKILL.md").write_text(content, encoding="utf-8")
    repo = FilesystemSkillRepository(tmp_path / "installed")

    for operation in (
        lambda: repo.read_skill_content("../outside"),
        lambda: repo.update_skill("../outside", "demo", _content()),
        lambda: repo.delete_skill("../outside"),
    ):
        with pytest.raises(ValueError):
            operation()
        assert (outside / "SKILL.md").read_text(encoding="utf-8") == content


@pytest.mark.parametrize("link_kind", ["directory", "file"])
def test_invalid_skill_fallback_rejects_symlinks_outside_managed_root(tmp_path, link_kind):
    outside = _create(tmp_path, "outside")
    content = "Invalid skill.\n"
    (outside / "SKILL.md").write_text(content, encoding="utf-8")
    root = tmp_path / "installed"
    root.mkdir()
    directory = root / "demo"
    try:
        if link_kind == "directory":
            directory.symlink_to(outside, target_is_directory=True)
        else:
            directory.mkdir()
            (directory / "SKILL.md").symlink_to(outside / "SKILL.md")
    except OSError as exc:
        pytest.skip(f"Symlinks are unavailable: {exc}")
    repo = FilesystemSkillRepository(root)

    for operation in (
        lambda: repo.read_skill_content("demo"),
        lambda: repo.update_skill("demo", "demo", _content()),
        lambda: repo.delete_skill("demo"),
    ):
        with pytest.raises(ValueError, match="escapes"):
            operation()
        assert (outside / "SKILL.md").read_text(encoding="utf-8") == content


def test_rename_imported_skill_preserves_resources(tmp_path):
    previous = _create(tmp_path, "folder-name", name="catalog-name")
    repo = FilesystemSkillRepository(tmp_path)
    updated = repo.update_skill("catalog-name", "renamed", _content("renamed", "New."))
    assert not previous.exists()
    assert updated.path == tmp_path / "renamed"
    assert updated.resources == ["scripts/run.py"]
    assert repo.read_skill_content("renamed") == _content("renamed", "New.")
    assert repo.load_skill("catalog-name") is None


def test_imported_noncanonical_identity_can_be_read_and_repaired(tmp_path):
    directory = _create(tmp_path, "Legacy Folder", name="Legacy Name")
    repo = FilesystemSkillRepository(tmp_path)
    assert repo.read_skill_content("Legacy Name") == _content("Legacy Name")
    repaired = repo.update_skill("Legacy Name", "demo", _content(body="Repaired."))
    assert repaired.name == "demo"
    assert not directory.exists()
    assert repo.load_skill("demo").body == "Repaired."


def test_rename_can_adopt_the_existing_directory_name(tmp_path):
    directory = _create(tmp_path, "demo", name="imported")
    repo = FilesystemSkillRepository(tmp_path)
    updated = repo.update_skill("imported", "demo", _content())
    assert updated.path == directory
    assert repo.load_skill("imported") is None
    assert repo.read_skill_content("demo") == _content()


def test_failed_create_leaves_no_partial_skill(tmp_path, monkeypatch):
    def fail_write(path, target):
        raise OSError("Write failed")

    monkeypatch.setattr(Path, "replace", fail_write)
    repo = FilesystemSkillRepository(tmp_path)
    with pytest.raises(OSError, match="Write failed"):
        repo.create_skill("demo", _content())
    assert not (tmp_path / "demo").exists()
    assert repo.list_summaries() == []


def test_create_rejects_existing_catalog_identity_with_different_directory(tmp_path):
    _create(tmp_path, "imported-folder", name="demo")
    with pytest.raises(FileExistsError):
        FilesystemSkillRepository(tmp_path).create_skill("demo", _content())
    assert not (tmp_path / "demo").exists()


def test_failed_update_restores_name_and_content(tmp_path, monkeypatch):
    previous = _create(tmp_path)
    original_replace = Path.replace

    def fail_write(path, target):
        if Path(target).name == "SKILL.md":
            raise OSError("Write failed")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_write)
    repo = FilesystemSkillRepository(tmp_path)
    with pytest.raises(OSError, match="Write failed"):
        repo.update_skill("demo", "renamed", _content("renamed", "New."))
    assert (previous / "SKILL.md").read_text(encoding="utf-8") == _content()
    assert not (tmp_path / "renamed").exists()
    assert not list(previous.glob(".SKILL-*"))


def test_shared_repositories_observe_create_update_rename_and_delete(tmp_path):
    left = FilesystemSkillRepository(tmp_path)
    right = FilesystemSkillRepository(tmp_path)
    assert right.list_summaries() == []
    left.create_skill("demo", _content())
    assert right.load_skill("demo").body == "Original body."
    left.update_skill("demo", "demo", _content(body="Updated."))
    assert right.load_skill("demo").body == "Updated."
    left.update_skill("demo", "renamed", _content("renamed"))
    assert right.load_skill("demo") is None
    assert right.load_skill("renamed") is not None
    left.delete_skill("renamed")
    assert right.list_summaries() == []


def test_repository_observes_external_file_changes_without_revision(tmp_path):
    directory = _create(tmp_path)
    repo = FilesystemSkillRepository(tmp_path)
    assert repo.load_skill("demo").body == "Original body."
    (directory / "SKILL.md").write_text(_content(body="External replacement with a different size."), encoding="utf-8")
    assert repo.load_skill("demo").body == "External replacement with a different size."
    (directory / "SKILL.md").unlink()
    assert repo.list_summaries() == []


def test_transient_read_failure_is_retried_without_a_disk_change(tmp_path, monkeypatch):
    _create(tmp_path)
    original_read = Path.read_text
    failed = False

    def fail_once(path, *args, **kwargs):
        nonlocal failed
        if path.name == "SKILL.md" and not failed:
            failed = True
            raise OSError("Transient read failure")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_once)
    repo = FilesystemSkillRepository(tmp_path)
    assert repo.load_skill("demo") is None
    assert repo.load_skill("demo").body == "Original body."


def test_parser_does_not_read_filesystem_and_resources_load_on_demand(tmp_path, monkeypatch):
    directory = _create(tmp_path)
    with monkeypatch.context() as context:
        context.setattr(Path, "rglob", lambda *args: pytest.fail("Unexpected resource scan"))
        skill = parse_skill_md(_content(), directory)
        assert skill.resources == []
        repo = FilesystemSkillRepository(tmp_path)
        assert repo.list_summaries()[0].name == "demo"
    assert repo.load_skill("demo").resources == ["scripts/run.py"]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend,root", [("modal", "/project-b"), ("daytona", "/project-a")])
async def test_workspace_cache_separates_roots_and_backends(fake_workspaces, backend, root):
    first = WorkspaceRef(backend="modal", locator="same-sandbox", metadata={"root": "/project-a"})
    second = WorkspaceRef(backend=backend, locator="same-sandbox", metadata={"root": root})
    fake_workspaces.files[fake_workspaces.key(first)] = {".agent/skills/demo/SKILL.md": _content(body="First.")}
    fake_workspaces.files[fake_workspaces.key(second)] = {".agent/skills/demo/SKILL.md": _content(body="Second.")}
    kwargs = {"repository_dir": ".agent/skills", "thread_id": "same-thread"}
    a = await discovery.discover_repository_skills(workspace=first, **kwargs)
    b = await discovery.discover_repository_skills(workspace=second, **kwargs)
    assert a["demo"].body == "First."
    assert b["demo"].body == "Second."


@pytest.mark.asyncio
@pytest.mark.parametrize("scan", [False, True])
async def test_repository_discovery_validates_directories_in_both_branches(fake_workspaces, monkeypatch, scan):
    from agent.modules.skills.package_io import WorkspacePackageIO

    ref = WorkspaceRef(backend="modal", locator="sandbox", metadata={"root": "/repo"})
    files = {f".agent/skills/{directory}/SKILL.md": _content(name=name)
             for directory, name in [("demo", "demo"), ("Demo", "uppercase-dir"), ("my_skill", "underscore-dir")]}
    fake_workspaces.files[fake_workspaces.key(ref)] = files
    if scan:
        original = workspaces.get_workspace_browser

        async def browser(workspace, **kwargs):
            result = await original(workspace, **kwargs)
            result.tree_page = lambda *args, **kwargs: None
            return result

        async def operation(self, op, path):
            assert op == "scan" and path == ".agent/skills"
            return {"skills": [{"directory": str(Path(file).parent).replace("\\", "/"), "content": content}
                               for file, content in files.items()]}

        monkeypatch.setattr(workspaces, "get_workspace_browser", browser)
        monkeypatch.setattr(WorkspacePackageIO, "operation", operation)
    skills = await discovery.discover_repository_skills(workspace=ref, repository_dir=".agent/skills")
    assert list(skills) == ["demo"]


@pytest.mark.asyncio
async def test_workspace_resources_use_backend_and_are_loaded_only_for_content(tmp_path, monkeypatch, fake_workspaces):
    monkeypatch.chdir(tmp_path)
    host = _create(tmp_path / ".agent" / "skills")
    assert (host / "scripts" / "run.py").exists()
    ref = WorkspaceRef(backend="modal", locator="sandbox", metadata={"root": "/repo"})
    fake_workspaces.files[fake_workspaces.key(ref)] = {
        ".agent/skills/demo/SKILL.md": _content(body="Remote."),
        ".agent/skills/demo/scripts/nested/remote.py": "print(1)",
        ".agent/skills/demo/references/guide.md": "Guide",
        ".agent/skills/demo/assets/image.png": "Image",
    }
    service = SkillsService(FilesystemSkillRepository(tmp_path / "global"))
    kwargs = {"names": [], "workspace": ref, "repository_dir": ".agent/skills", "thread_id": "t1"}
    catalog = await service.catalog_xml(**kwargs)
    assert "<name>demo</name>" in catalog
    assert [path for _, path in fake_workspaces.trees] == [".agents/skills", ".agent/skills"]
    xml = await service.content_xml("demo", **kwargs)
    assert "Remote." in xml
    assert "scripts/nested/remote.py" in xml
    assert "references/guide.md" in xml
    assert "assets/image.png" in xml
    assert "scripts/run.py" not in xml
    assert len(fake_workspaces.reads) == 1


@pytest.mark.asyncio
async def test_local_workspace_content_includes_nested_resources(tmp_path):
    directory = _create(tmp_path / "workspace" / ".agent" / "skills")
    reference = directory / "references" / "nested" / "guide.md"
    reference.parent.mkdir(parents=True)
    reference.write_text("Guide", encoding="utf-8")
    service = SkillsService(FilesystemSkillRepository(tmp_path / "global"))
    xml = await service.content_xml(
        "demo", names=[], workspace=str(tmp_path / "workspace"),
        repository_dir=".agent/skills", thread_id="t1",
    )
    assert "scripts/run.py" in xml
    assert "references/nested/guide.md" in xml


@pytest.mark.asyncio
async def test_recursive_workspace_resource_aliases_have_a_scan_limit(monkeypatch):
    import agent.modules.skills.resources as resources_module

    monkeypatch.setattr(resources_module, "MAX_RESOURCE_DIRECTORIES", 4)
    paths = []

    class Browser:
        async def tree(self, path):
            paths.append(path)
            name = "scripts" if len(paths) == 1 else "loop"
            return {"entries": [{"kind": "directory", "name": name}]}

    assert await list_workspace_resources(Browser(), ".agent/skills/demo") == []
    assert len(paths) == 4


@pytest.mark.asyncio
async def test_invalidation_retries_inflight_discovery_before_returning(fake_workspaces):
    ref = WorkspaceRef(backend="modal", locator="sandbox", metadata={"root": "/repo"})
    files = {".agent/skills/demo/SKILL.md": _content(body="Old.")}
    fake_workspaces.files[fake_workspaces.key(ref)] = files
    started = asyncio.Event()
    release = asyncio.Event()

    async def on_read():
        if not started.is_set():
            started.set()
            await release.wait()

    fake_workspaces.on_read = on_read
    kwargs = {"workspace": ref, "repository_dir": ".agent/skills"}
    task = asyncio.create_task(discovery.discover_repository_skills(**kwargs))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        files[".agent/skills/demo/SKILL.md"] = _content(body="New.")
        reload_repository_skills()
        release.set()
        result = await asyncio.wait_for(task, timeout=2)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert result["demo"].body == "New."
    assert (await discovery.discover_repository_skills(**kwargs))["demo"].body == "New."
    assert len(fake_workspaces.reads) == 2


@pytest.mark.asyncio
async def test_discovery_cache_is_bounded_and_expires(fake_workspaces, monkeypatch):
    now = [10.0]
    monkeypatch.setattr(discovery.time, "monotonic", lambda: now[0])
    kwargs = {"repository_dir": ".agent/skills"}
    refs = []
    for index in range(discovery.MAX_DISCOVERY_CACHE_ENTRIES + 1):
        ref = WorkspaceRef(backend="modal", locator=f"sandbox-{index}", metadata={"root": "/repo"})
        refs.append(ref)
        fake_workspaces.files[fake_workspaces.key(ref)] = {".agent/skills/demo/SKILL.md": _content()}
        await discovery.discover_repository_skills(workspace=ref, **kwargs)
    count = len(fake_workspaces.reads)
    await discovery.discover_repository_skills(workspace=refs[0], **kwargs)
    assert len(fake_workspaces.reads) == count + 1
    now[0] += discovery.DISCOVERY_TTL_SECONDS + 1
    fake_workspaces.files[fake_workspaces.key(refs[0])][".agent/skills/demo/SKILL.md"] = _content(body="Changed.")
    result = await discovery.discover_repository_skills(workspace=refs[0], **kwargs)
    assert result["demo"].body == "Changed."


def test_content_wrapper_quotes_imported_skill_identifier():
    name = 'imported"identifier'
    skill = Skill(name=name, description="Test.", body="Plain body.", path=Path("demo"))
    xml = skill_content_xml(skill)
    assert ElementTree.fromstring(xml).attrib["name"] == name
