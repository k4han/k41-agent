"""Real offline dependency execution in package snapshots, with reusable caches."""

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

import pytest

from agent.modules.skills.packages import SkillPackages
from agent.modules.skills.package_worker import native_path
from agent.modules.skills.repository import FilesystemSkillRepository
from agent.shared.infrastructure.subprocess_utils import hidden_subprocess_kwargs


def create_wheel(path):
    entries = {
        "skill_fixture/__init__.py": b"VALUE = 53\n",
        "skill_fixture-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: skill-fixture\nVersion: 1.0\n",
        "skill_fixture-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: skill-tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    records = []
    for name, content in entries.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
        records.append(f"{name},sha256={digest},{len(content)}")
    records.append("skill_fixture-1.0.dist-info/RECORD,,")
    entries["skill_fixture-1.0.dist-info/RECORD"] = ("\n".join(records) + "\n").encode()
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)


@pytest.fixture
def runtime_package(tmp_path, isolated_container):
    source = tmp_path / "skills" / "runtime-demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: runtime-demo\ndescription: Runtime check.\n---\nRun the script.\n")
    isolated_container._skill_repository = FilesystemSkillRepository(source.parent)
    packages = SkillPackages(isolated_container.skill_repository)
    isolated_container._skill_packages = packages
    # Windows shells require a short cwd even when file APIs support long paths.
    with tempfile.TemporaryDirectory(prefix="sr-", dir=Path.cwd() / ".tmp_pytest" / "runtime") as directory:
        try:
            yield source, Path(directory), packages
        finally:
            target = native_path(Path(directory))
            assert target.is_relative_to(native_path(Path.cwd() / ".tmp_pytest" / "runtime"))
            shutil.rmtree(target)


@pytest.mark.asyncio
@pytest.mark.parametrize("declaration", ["pep723", "with", "requirements"])
async def test_uv_explicit_dependencies_reuse_cache_without_source_changes(runtime_package, tmp_path, declaration):
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv is required")
    source, workspace, packages = runtime_package
    wheel = source / "skill_fixture-1.0-py3-none-any.whl"
    create_wheel(wheel)
    metadata = f'# /// script\n# dependencies = ["skill-fixture @ {wheel.as_uri()}"]\n# ///\n' if declaration == "pep723" else ""
    (source / "run.py").write_text(metadata + "import skill_fixture, sys\nprint(skill_fixture.VALUE)\nprint(sys.prefix)\n")
    (source / "requirements.txt").write_text(wheel.as_uri() + "\n")
    original = {path.name: path.read_bytes() for path in source.iterdir()}
    activated = await packages.activate("runtime-demo", workspace=str(workspace), thread_id="runtime-thread", agent_name="default")
    root = Path(activated.skill_root)
    command = [uv, "run", "--offline", "--cache-dir", str(tmp_path / "uv-cache"), "--no-project", "--python", sys.executable]
    if declaration == "with":
        command.extend(["--with", str(root / wheel.name)])
    elif declaration == "requirements":
        command.extend(["--with-requirements", str(root / "requirements.txt")])
    command.extend(["--script", str(root / "run.py")])
    results = []
    cache_files = []
    for _ in range(2):
        results.append(subprocess.run(command, cwd=workspace, capture_output=True, timeout=45, **hidden_subprocess_kwargs()))
        cache_files.append(set((tmp_path / "uv-cache" / "archive-v0").glob("*/skill_fixture/__init__.py")))
    for result in results:
        assert result.returncode == 0, result.stderr.decode()
        assert result.stdout.splitlines()[0] == b"53"
    assert cache_files[0] and cache_files[0] == cache_files[1]
    if declaration == "pep723":
        assert results[0].stdout == results[1].stdout
    assert {path.name: path.read_bytes() for path in source.iterdir()} == original
    assert not (workspace / ".venv").exists()


@pytest.mark.asyncio
async def test_node_manifest_lockfile_and_offline_install_are_snapshot_local(runtime_package):
    node, pnpm = shutil.which("node"), shutil.which("pnpm")
    if not node or not pnpm:
        pytest.skip("Node.js and pnpm are required")
    source, workspace, packages = runtime_package
    dependency = source / "fixture"
    dependency.mkdir()
    (dependency / "package.json").write_text(json.dumps({"name": "skill-fixture", "version": "1.0.0", "main": "index.js"}))
    (dependency / "index.js").write_text("module.exports = 53;\n")
    (source / "package.json").write_text(json.dumps({"name": "runtime-demo", "private": True, "dependencies": {"skill-fixture": "file:./fixture"}}))
    (source / "run.cjs").write_text("console.log(require('skill-fixture'));\n")
    original = (source / "package.json").read_bytes()
    activated = await packages.activate("runtime-demo", workspace=str(workspace), thread_id="runtime-thread", agent_name="default")
    root = Path(activated.skill_root)
    environment = {**os.environ, "CI": "true"}
    for arguments in (["--ignore-workspace", "install", "--offline", "--lockfile-only", "--no-frozen-lockfile"],
                      ["--ignore-workspace", "install", "--offline", "--frozen-lockfile"]):
        result = subprocess.run([pnpm, *arguments], cwd=str(root).removeprefix("\\\\?\\"), shell=os.name == "nt",
                                env=environment, capture_output=True, timeout=60, **hidden_subprocess_kwargs())
        assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    output = subprocess.run([node, str(root / "run.cjs")], cwd=workspace, capture_output=True, timeout=15, **hidden_subprocess_kwargs())
    assert output.returncode == 0, output.stderr.decode()
    assert output.stdout.strip() == b"53"
    assert (source / "package.json").read_bytes() == original
    assert not (source / "node_modules").exists() and not (workspace / "node_modules").exists()
    assert (root / "pnpm-lock.yaml").is_file()
