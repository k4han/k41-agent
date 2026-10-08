"""Exercise the actual portable worker through both sandbox transport adapters."""

import json
from dataclasses import asdict
from pathlib import Path
import shlex
import shutil
from types import SimpleNamespace

import pytest

import agent.modules.skills.package_io as transport_module
from agent.modules.skills.package_io import WorkspacePackageIO
from agent.modules.skills.package_worker import run_operation, pack_directory
from agent.modules.skills.packages import SkillPackages
from agent.modules.skills.repository import FilesystemSkillRepository
from agent.modules.workspaces import WorkspaceRef


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["daytona", "modal"])
async def test_sandbox_package_transport_preserves_bytes_and_recovers(backend, tmp_path, monkeypatch, isolated_container):
    root = tmp_path / "sandbox"
    root.mkdir()
    ref = WorkspaceRef(backend=backend, locator="sandbox-1", metadata={"root": root.as_posix()})
    requests = []

    class Backend:
        def __init__(self):
            self.ref = ref

        async def write_bytes(self, path, content):
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

        async def read_bytes(self, path):
            return (root / path).read_bytes()

        async def execute(self, command, **kwargs):
            if command.startswith("rm -f"):
                for path in shlex.split(command)[3:]:
                    Path(path).unlink(missing_ok=True)
            else:
                _, worker, request_path, response_path = shlex.split(command)
                request = json.loads(Path(request_path).read_bytes())
                requests.append(request["request"]["op"])
                try:
                    result = {"result": run_operation(request["workspace"], request["request"])}
                except Exception as exc:
                    result = {"error": {"type": type(exc).__name__, "message": str(exc)}}
                Path(response_path).write_text(json.dumps(result), encoding="utf-8")
            return SimpleNamespace(exit_code=0, output="")

    backend_instance = Backend()

    async def factory(*args, **kwargs):
        return backend_instance

    monkeypatch.setattr(transport_module, "get_workspace_file_io", factory)
    transport = WorkspacePackageIO(ref, thread_id="thread-1")
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: demo\ndescription: A package.\n---\nBody\n")
    (source / "data.bin").write_bytes(bytes(range(256)))
    archive = pack_directory(source)
    await transport.install([(".agent/skills/demo", archive)])
    assert (await transport.operation("scan", ".agent/skills"))["skills"][0]["directory"] == ".agent/skills/demo"
    data, info = await transport.read(".agent/skills/demo/data.bin")
    assert data == bytes(range(256))
    await transport.write(".agent/skills/demo/data.bin", b"\xff\x00", expected_version=info["version"])
    exported, _ = await transport.archive(".agent/skills/demo")
    assert exported
    assert {"install", "scan", "read", "write", "archive"}.issubset(requests)
    assert not list((root / ".k41-agent" / "package-runtime").glob("*.request"))
    with pytest.raises(FileNotFoundError):
        await transport.read("missing")

    repository = FilesystemSkillRepository(tmp_path / "shared-skills")
    isolated_container._skill_repository = repository
    packages = SkillPackages(repository)
    isolated_container._skill_packages = packages
    first = await packages.activate("demo", workspace=ref, thread_id="one", agent_name="default")
    installs = requests.count("install")
    second = await packages.activate("demo", workspace=ref, thread_id="two", agent_name="default")
    assert requests.count("install") == installs
    assert first.skill_root == second.skill_root
    assert first.package_path.startswith(".k41-agent/s/sk-")
    assert len(Path(first.skill_root).name) == 23
    assert first.id not in first.skill_root and first.version not in first.skill_root

    shutil.rmtree(root / first.package_path)
    origin, info = await transport.read(".agent/skills/demo/data.bin")
    await transport.write(".agent/skills/demo/data.bin", b"changed origin", expected_version=info["version"])
    restored = await packages.restore(asdict(first), workspace=ref, thread_id="one")
    assert restored["version"] == first.version
    assert (root / first.package_path / "data.bin").read_bytes() == origin
    assert "ensure_runtime" in requests
