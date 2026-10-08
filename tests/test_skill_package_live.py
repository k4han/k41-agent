"""Opt-in integration checks against explicitly supplied, existing sandboxes."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import shlex
import uuid

import pytest

from agent.modules.skills.package_io import WorkspacePackageIO
from agent.modules.skills.packages import SkillPackages
from agent.modules.skills.repository import FilesystemSkillRepository
from agent.modules.tools.coding.storage import conversation_key
from agent.modules.workspaces import get_workspace_file_io, WorkspaceRef


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["daytona", "modal"])
async def test_live_skill_snapshot_runtime_and_restore(backend, tmp_path, isolated_container):
    value = os.environ.get(f"K41_TEST_{backend.upper()}_WORKSPACE")
    if not value:
        pytest.skip(f"Set K41_TEST_{backend.upper()}_WORKSPACE to an existing sandbox WorkspaceRef JSON.")
    ref = WorkspaceRef.model_validate(json.loads(value))
    assert ref.backend == backend
    name = "integration-" + uuid.uuid4().hex[:12]
    source = tmp_path / "skills" / name
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Runtime integration check.\n---\nUse the bundled script.\n", encoding="utf-8")
    payload = bytes(range(256))
    (source / "asset.bin").write_bytes(payload)
    (source / "copy.py").write_text(
        '# /// script\n# dependencies = []\n# ///\nfrom pathlib import Path\nimport sys\n'
        'Path(sys.argv[1]).write_bytes((Path(__file__).parent / "asset.bin").read_bytes())\n', encoding="utf-8")
    isolated_container._skill_repository = FilesystemSkillRepository(source.parent)
    packages = SkillPackages(isolated_container.skill_repository)
    isolated_container._skill_packages = packages
    thread = "skill-integration-" + uuid.uuid4().hex
    transport = WorkspacePackageIO(ref, thread_id=thread)
    output = f".k41-agent/{name}.bin"
    try:
        loaded = await packages.activate(name, workspace=ref, thread_id=thread, agent_name="default")
        assert (await transport.read(loaded.package_path + "/asset.bin"))[0] == payload
        runtimes = await transport.operation("runtime_status")
        uv = runtimes["tools"].get("uv") or f"{loaded.workspace_root}/.k41-agent/skill-tools/bin/uv"
        io = await get_workspace_file_io(transport.ref, thread_id=thread)
        command = " ".join(shlex.quote(item) for item in (uv, "run", "--no-project", "--script",
            loaded.skill_root + "/copy.py", loaded.workspace_root + "/" + output))
        result = await io.execute(command, timeout=120)
        assert result.exit_code == 0, result.output
        assert (await transport.read(output))[0] == payload
        destination = loaded.package_path
        await transport.operation("delete", destination)
        (source / "asset.bin").write_bytes(b"changed source")
        await packages.restore(asdict(loaded), workspace=transport.ref, thread_id=thread)
        assert (await transport.read(destination + "/asset.bin"))[0] == payload
    finally:
        try:
            _, info = await transport.read(output)
            await transport.operation("delete", output, expected_version=info["version"])
        except FileNotFoundError:
            pass
        if 'loaded' in locals():
            await transport.operation("delete", loaded.package_path)
        packages.snapshots.release(conversation_key(thread))
