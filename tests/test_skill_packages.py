"""Package lifecycle and activation regression tests."""

from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
import stat
import zipfile

import pytest

from agent.modules.skills.package_worker import pack_directory, unpack_archive, run_operation
import agent.modules.skills.packages as packages_module
from agent.modules.skills.packages import SkillPackages, validate_document
from agent.modules.skills.repository import FilesystemSkillRepository
from agent.modules.skills.resources import list_local_resources


CONTENT = "---\nname: demo\ndescription: Test package.\nmetadata:\n  version: '1'\ncustom-field:\n  nested: true\n---\nRead docs/guide.md and run scripts/process.py.\n"


@pytest.fixture
def packages(tmp_path, isolated_container):
    root = tmp_path / "skills"
    skill = root / "demo"
    (skill / "docs").mkdir(parents=True)
    (skill / "scripts").mkdir()
    (skill / "SKILL.md").write_text(CONTENT, encoding="utf-8")
    (skill / "docs" / "guide.md").write_text("# A guide\n", encoding="utf-8")
    (skill / "scripts" / "process.py").write_text("print('hello')\n", encoding="utf-8")
    (skill / "template.bin").write_bytes(bytes(range(256)))
    repo = FilesystemSkillRepository(root)
    isolated_container._skill_repository = repo
    service = SkillPackages(repo)
    isolated_container._skill_packages = service
    return service


def test_discovery_lists_files_outside_conventional_directories(packages):
    assert list_local_resources(packages.repository.root / "demo") == ["docs/guide.md", "scripts/process.py", "template.bin"]


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["global", "project"])
@pytest.mark.parametrize("document", [CONTENT.encode("utf-16"), b"\xffinvalid"], ids=["utf16", "invalid-utf8"])
async def test_invalid_document_encoding_does_not_block_other_skills(packages, tmp_path, scope, document):
    from agent.modules.skills import get_effective_skills_catalog_xml

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = packages.repository.root if scope == "global" else workspace / ".agent" / "skills"
    broken = root / "broken"
    broken.mkdir(parents=True)
    (broken / "SKILL.md").write_bytes(document)

    inventory = {item.name: item for item in await packages.inventory(str(workspace))}
    assert inventory["demo"].enabled
    assert not inventory["broken"].enabled
    assert any(item.code == "invalid_package" and "UTF-8" in item.message for item in inventory["broken"].diagnostics)
    catalog = await get_effective_skills_catalog_xml(workspace=str(workspace))
    assert "<name>demo</name>" in catalog
    assert "<name>broken</name>" not in catalog
    loaded = await packages.activate("demo", workspace=str(workspace), thread_id="encoding-thread", agent_name="default")
    assert loaded.name == "demo"


def test_document_preserves_unknown_fields():
    document = validate_document(CONTENT, name="demo", strict=True)
    assert document["valid"]
    assert document["frontmatter"]["custom-field"] == {"nested": True}
    assert any(item["code"] == "unsupported_field" for item in document["diagnostics"])


@pytest.mark.parametrize("frontmatter,field", [
    ("description: Valid", "name"),
    ("name: demo\ndescription: Valid\ncompatibility: ''", "compatibility"),
    ("name: demo\ndescription: Valid\nallowed-tools: [read]", "allowed-tools"),
])
def test_new_document_requires_standard_field_types(frontmatter, field):
    result = validate_document("---\n" + frontmatter + "\n---\nBody", name="demo", strict=True)
    assert not result["valid"]
    assert result["diagnostics"][0]["field"] == field


def test_standard_unicode_lowercase_skill_names():
    from agent.modules.skills.repository import normalize_skill_name
    name = "\u00e9tude"
    assert normalize_skill_name(name) == name
    assert validate_document(f"---\nname: {name}\ndescription: Valid\n---\nBody", name=name, strict=True)["valid"]


@pytest.mark.parametrize("field,value", [("description", "x" * 1025), ("description", "42"), ("compatibility", "x" * 501)])
def test_document_strict_limits(field, value):
    content = f"---\nname: demo\ndescription: Valid\n{field}: {value}\n---\nBody"
    assert not validate_document(content, name="demo", strict=True)["valid"]


def test_archive_preserves_bytes_and_modes(packages, tmp_path):
    source = packages.repository.root / "demo"
    script = source / "scripts" / "process.py"
    script.chmod(0o755)
    data = pack_directory(source)
    destination = tmp_path / "copy"
    unpack_archive(data, destination)
    assert (destination / "template.bin").read_bytes() == bytes(range(256))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.getinfo("scripts/process.py").external_attr >> 16 & 0o777 == script.stat().st_mode & 0o777
    assert data == pack_directory(source)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/drive", "AUX.txt", "a/../../bad", "a./bad"])
def test_archive_rejects_nonportable_paths(name, tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, b"data")
    with pytest.raises(ValueError):
        unpack_archive(buffer.getvalue(), tmp_path / "copy")


def test_archive_rejects_symlinks_and_case_conflicts(tmp_path):
    for symbolic in (True, False):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            if symbolic:
                info = zipfile.ZipInfo("link")
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, b"../outside")
            else:
                archive.writestr("a.txt", b"one")
                archive.writestr("A.txt", b"two")
        with pytest.raises(ValueError):
            unpack_archive(buffer.getvalue(), tmp_path / str(symbolic))


def test_archive_limits(packages, tmp_path):
    data = pack_directory(packages.repository.root / "demo")
    with pytest.raises(ValueError):
        unpack_archive(data, tmp_path / "limited", {"max_bytes": 2})


@pytest.mark.asyncio
async def test_catalog_version_tracks_every_resource(packages):
    before = (await packages.inventory())[0].version
    source = packages.repository.root / "demo"
    assert before == hashlib.sha256(pack_directory(source)).hexdigest()
    (source / "docs" / "guide.md").write_text("Changed resource")
    after = (await packages.inventory())[0].version
    assert after != before
    assert after == hashlib.sha256(pack_directory(source)).hexdigest()


@pytest.mark.asyncio
async def test_inventory_precedence_and_disabled(packages, tmp_path, isolated_container):
    workspace = tmp_path / "workspace"
    for directory in (".agents", ".agent"):
        skill = workspace / directory / "skills" / "demo"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(CONTENT)
    items = await packages.inventory(str(workspace))
    assert len(items) == 3
    assert items[0].source.directory == ".agent/skills/demo"
    assert [item.shadowed for item in items] == [False, True, True]
    await packages.enabled(items[0].id, False, str(workspace))
    items = await packages.inventory(str(workspace))
    assert not items[0].enabled
    assert not items[1].shadowed


def test_disabled_state_cache_reuses_reads_and_expires(packages, monkeypatch):
    state = packages.repository.root / ".skill-state.json"
    now = [10.0]
    reads = []
    original = Path.read_bytes

    def read(path):
        if path == state:
            reads.append(path)
        return original(path)

    monkeypatch.setattr(packages_module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(Path, "read_bytes", read)
    state.write_text(json.dumps({"demo-id": False}))
    for _ in range(5):
        assert "demo-id" in packages._disabled()
    assert len(reads) == 1
    state.write_text(json.dumps({"demo-id": True, "other-id": False}))
    assert "demo-id" in packages._disabled()
    now[0] += packages_module.DISABLED_STATE_TTL_SECONDS
    assert "demo-id" not in packages._disabled()
    assert "other-id" in packages._disabled()
    assert len(reads) == 2
    state.unlink()
    now[0] += packages_module.DISABLED_STATE_TTL_SECONDS
    assert "other-id" not in packages._disabled()


def test_disabled_state_cache_observes_revision_and_current_config(packages, monkeypatch, isolated_container):
    from agent.shared.infrastructure.revisions import SKILLS_REVISION, bump_revision

    state = packages.repository.root / ".skill-state.json"
    state.write_text(json.dumps({"demo-id": False}))
    assert "demo-id" in packages._disabled()
    state.write_text(json.dumps({"demo-id": True}))
    bump_revision(SKILLS_REVISION)
    assert "demo-id" not in packages._disabled()
    original = isolated_container.config_service.get
    monkeypatch.setattr(isolated_container.config_service, "get", lambda key, default=None:
        ["demo-id", "config-id"] if key == "skills.disabled_ids" else original(key, default))
    assert "demo-id" not in packages._disabled()
    assert "config-id" in packages._disabled()


@pytest.mark.asyncio
async def test_disabled_state_cache_invalidates_immediately_on_dashboard_changes(packages, tmp_path):
    from agent.modules.skills.sources import workspace_key

    identity = (await packages.inventory())[0].id
    active = {"id": identity, "name": "demo", "agent_name": "default", "workspace_key": workspace_key(str(tmp_path)), "source": {"scope": "global"}}
    options = {"workspace": str(tmp_path), "agent_name": "default"}
    assert packages.authorize_active(active, **options)
    await packages.enabled(identity, False)
    assert not packages.authorize_active(active, **options)
    await packages.enabled(identity, True)
    assert packages.authorize_active(active, **options)


@pytest.mark.asyncio
async def test_nested_global_package_is_available_to_catalog_and_content(packages):
    from agent.modules.skills import get_effective_skill_content_xml, get_effective_skills_catalog_xml

    root = packages.repository.root
    (root / "group").mkdir()
    (root / "demo").rename(root / "group" / "demo")
    selected = next(item for item in await packages.inventory(scope="global") if item.name == "demo")
    assert selected.enabled and not selected.shadowed
    catalog = await get_effective_skills_catalog_xml(allowed_names=["demo"])
    assert "<name>demo</name>" in catalog
    assert str(root / "group" / "demo" / "SKILL.md") in catalog
    content = await get_effective_skill_content_xml("demo", allowed_names=["demo"])
    assert content is not None and "Read docs/guide.md" in content
    assert "docs/guide.md" in content and "template.bin" in content
    assert "demo" not in await get_effective_skills_catalog_xml(allowed_names=[])
    assert await get_effective_skill_content_xml("demo", allowed_names=[]) is None
    await packages.enabled(selected.id, False)
    assert "demo" not in await get_effective_skills_catalog_xml()
    assert await get_effective_skill_content_xml("demo") is None


@pytest.mark.asyncio
async def test_global_catalog_and_content_follow_inventory_when_duplicate_is_disabled(packages):
    from agent.modules.skills import get_effective_skill_content_xml, get_effective_skills_catalog_xml

    duplicate = packages.repository.root / "other"
    duplicate.mkdir()
    (duplicate / "SKILL.md").write_text("---\nname: demo\ndescription: Duplicate fallback.\n---\nFallback instructions.\n")
    selected = next(item for item in await packages.inventory(scope="global") if item.enabled and not item.shadowed)
    await packages.enabled(selected.id, False)
    fallback = next(item for item in await packages.inventory(scope="global") if item.enabled and not item.shadowed)
    assert fallback.source.directory == "other"
    catalog = await get_effective_skills_catalog_xml()
    assert "<name>demo</name>" in catalog and "Duplicate fallback." in catalog
    content = await get_effective_skill_content_xml("demo")
    assert content is not None and "Fallback instructions." in content


@pytest.mark.asyncio
async def test_write_requires_current_version(packages):
    identity = (await packages.inventory())[0].id
    _, info = await packages.read_file(identity, "docs/guide.md")
    with pytest.raises(FileExistsError):
        await packages.write_file(identity, "docs/guide.md", b"changed")
    await packages.write_file(identity, "docs/guide.md", b"changed", expected_version=info["version"])
    with pytest.raises(FileExistsError):
        await packages.write_file(identity, "docs/guide.md", b"stale", expected_version=info["version"])


@pytest.mark.asyncio
async def test_activation_snapshot_and_recovery_preserve_pinned_version(packages, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    activated = await packages.activate("demo", workspace=str(workspace), thread_id="thread-1", agent_name="default")
    assert (Path(activated.skill_root) / "template.bin").read_bytes() == bytes(range(256))
    assert "uv run --no-project" in activated.content
    assert "docs/guide.md" in activated.content
    (packages.repository.root / "demo" / "docs" / "guide.md").write_text("Updated source")
    import shutil
    shutil.rmtree(Path(activated.skill_root))
    await packages.restore(asdict(activated), workspace=str(workspace), thread_id="thread-1")
    assert (Path(activated.skill_root) / "docs" / "guide.md").read_text() == "# A guide\n"
    refreshed = await packages.activate("demo", workspace=str(workspace), thread_id="thread-1", agent_name="default")
    assert refreshed.version != activated.version


@pytest.mark.asyncio
async def test_import_preview_conflict_and_export(packages, tmp_path):
    source = packages.repository.root / "demo"
    preview = await packages.preview(kind="directory", path=str(source))
    with pytest.raises(FileExistsError):
        await packages.import_preview(preview["preview_id"])
    await packages.import_preview(preview["preview_id"], overwrite=True)
    assert (source / "template.bin").read_bytes() == bytes(range(256))
    with pytest.raises(FileNotFoundError):
        await packages.import_preview(preview["preview_id"])


@pytest.mark.asyncio
async def test_delete_package_removes_all_resources(packages):
    identity = (await packages.inventory())[0].id
    await packages.remove(identity)
    assert not (packages.repository.root / "demo").exists()


def test_install_batch_conflict_rolls_back(packages, tmp_path):
    from base64 import b64encode
    destination = tmp_path / "target"
    destination.mkdir()
    existing = destination / "existing"
    existing.mkdir()
    (existing / "SKILL.md").write_text("original")
    archive = b64encode(pack_directory(packages.repository.root / "demo")).decode()
    with pytest.raises(FileExistsError):
        run_operation(str(destination), {"op": "install", "packages": [{"path": "new", "data": archive}, {"path": "existing", "data": archive}]})
    assert not (destination / "new").exists()
    assert (existing / "SKILL.md").read_text() == "original"


def test_tree_paginates_more_than_backend_limit(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    for index in range(510):
        (root / f"file-{index:03}").write_bytes(b"")
    first = run_operation(str(root), {"op": "tree", "limit": 500})
    second = run_operation(str(root), {"op": "tree", "offset": first["next_offset"]})
    assert len(first["entries"]) + len(second["entries"]) == 510
    assert second["next_offset"] is None


def test_snapshot_cleanup_protects_live_process_leases(tmp_path):
    import os
    from agent.modules.skills.package_worker import native_path
    root = native_path(tmp_path)
    protected = root / ".k41-agent" / "skills" / "conversation-a"
    protected.mkdir(parents=True)
    lease = protected / ".skill-lease-test"
    lease.write_text(str(os.getpid()))
    unused = protected.parent / "conversation-b"
    unused.mkdir()
    result = run_operation(str(tmp_path), {"op": "cleanup", "path": ".k41-agent/skills", "all": True})
    assert result["removed"] == ["conversation-b"]
    assert protected.is_dir()
    lease.unlink()
    result = run_operation(str(tmp_path), {"op": "cleanup", "path": ".k41-agent/skills", "all": True})
    assert result["removed"] == ["conversation-a"]


def test_snapshot_store_keeps_historical_versions_until_conversation_deletion(tmp_path):
    import os
    import time
    from agent.modules.skills.snapshots import SnapshotStore
    store = SnapshotStore(tmp_path / "snapshots")
    first = store.retain(b"first version", "conversation")
    second = store.retain(b"second version", "conversation")
    aged = time.time() - 8 * 86400
    for version in (first, second):
        os.utime(store.root / f"{version}.zip", (aged, aged))
    store.retain(b"other version", "other-conversation")
    assert store.read(first) == b"first version"
    assert store.read(second) == b"second version"
    store.release("conversation")
    assert not (store.root / f"{first}.zip").exists()
    assert not (store.root / f"{second}.zip").exists()


def test_install_failure_during_commit_restores_overwritten_packages(packages, tmp_path, monkeypatch):
    from base64 import b64encode
    from agent.modules.skills.package_worker import native_path
    root = native_path(tmp_path / "target")
    original = root / "first"
    original.mkdir(parents=True)
    (original / "SKILL.md").write_bytes(b"original version")
    archive = b64encode(pack_directory(packages.repository.root / "demo")).decode()
    rename = Path.rename

    def interrupted_rename(path, destination):
        if path.name == "1" and path.parent.name.startswith(".skill-install-"):
            raise PermissionError("Simulated interrupted import")
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", interrupted_rename)
    with pytest.raises(PermissionError):
        run_operation(str(root), {"op": "install", "overwrite": True, "packages": [
            {"path": "first", "data": archive}, {"path": "second", "data": archive}]})
    assert (original / "SKILL.md").read_bytes() == b"original version"
    assert not (root / "second").exists()


@pytest.mark.asyncio
async def test_git_preview_pins_commit_ref_and_subdirectory(packages, tmp_path, monkeypatch):
    source = tmp_path / "git-source" / "nested" / "git-demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: git-demo\ndescription: Git package.\n---\nInstructions\n")
    (source / "asset.bin").write_bytes(b"\x00\xff")
    calls = []
    commit = "a" * 40

    async def git(arguments, environment=None):
        import shutil
        calls.append(arguments)
        if arguments[0] == "clone":
            shutil.copytree(tmp_path / "git-source", arguments[-1])
        return commit if "rev-parse" in arguments else ""

    monkeypatch.setattr(packages, "_git", git)
    preview = await packages.preview(kind="git", url="https://github.com/example/repo.git", ref="v1.0", subdirectory="nested")
    assert preview["commit"] == commit
    assert calls[1][-1] == "v1.0"
    assert calls[-1][-1] == commit
    result = await packages.import_preview(preview["preview_id"])
    assert result["commit"] == commit
    assert (packages.repository.root / "git-demo" / "asset.bin").read_bytes() == b"\x00\xff"


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [
    {"url": "http://github.com/example/repo"},
    {"url": "https://token@github.com/example/repo"},
    {"url": "https://github.com/example/repo", "ref": "--unsafe"},
    {"url": "https://example.com/repo", "installation_id": 1},
])
async def test_git_import_rejects_unsupported_credentials_and_refs(packages, options):
    with pytest.raises(ValueError):
        await packages.preview(kind="git", **options)
