"""Execution locations, live sources, shell variables and Windows path regressions."""

from dataclasses import asdict
import asyncio
import errno
import json
import os
from pathlib import Path
import shutil
import shlex
import sys
import time

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END, StateGraph
import pytest

from agent.modules.skills.context import active_context, activation_key
from agent.modules.skills.execution import execution_path, shell_guidance
from agent.modules.skills.package_io import WorkspacePackageIO
from agent.modules.skills.package_worker import native_path, run_operation
from agent.modules.skills.packages import SkillPackages
from agent.modules.skills.repository import FilesystemSkillRepository
from agent.modules.tools.coding.models import CodingError, InvocationContext, PermissionRule
from agent.modules.tools.coding.permissions import Permissions
from agent.modules.tools.coding.processes import ProcessManager
from agent.modules.tools.coding.storage import OutputStore, conversation_key
from agent.modules.workflows.nodes.tool import tool_node
from agent.modules.workflows.run_config import WorkflowContext
from agent.modules.workflows.state.base import BaseState


@pytest.fixture
def execution(tmp_path, isolated_container):
    source = tmp_path / "skills" / "demo"
    (source / "scripts").mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: demo\ndescription: Execution check.\n---\nRun the bundled scripts.\n")
    (source / "scripts" / "read_asset.py").write_text(
        "from pathlib import Path\nimport sys\n"
        "Path(sys.argv[1]).write_bytes((Path(__file__).parent.parent / 'asset.bin').read_bytes())\n")
    (source / "asset.bin").write_bytes(b"\x00\xff")
    workspace = tmp_path / "workspace-storage" / "temp-workspaces" / ("thread-" + "a" * 32)
    workspace.mkdir(parents=True)
    isolated_container._skill_repository = FilesystemSkillRepository(source.parent)
    service = SkillPackages(isolated_container.skill_repository)
    isolated_container._skill_packages = service
    return source, workspace, service


def configure(monkeypatch, container, **settings):
    original = container.config_service.get
    monkeypatch.setattr(container.config_service, "get", lambda key, default=None: settings.get(key, original(key, default)))


@pytest.mark.asyncio
async def test_snapshots_are_short_and_shared_across_conversations_and_workspaces(execution):
    source, workspace, service = execution
    first = await service.activate("demo", workspace=str(workspace), thread_id="one", agent_name="default")
    manifest = Path(first.skill_root) / ".skill-manifest.json"
    timestamp = manifest.stat().st_mtime_ns
    other = workspace.parent / "other-workspace"
    other.mkdir()
    second = await service.activate("demo", workspace=str(other), thread_id="two", agent_name="default")
    assert first.skill_root == second.skill_root
    assert manifest.stat().st_mtime_ns == timestamp
    assert len(Path(first.skill_root).name) == 23
    assert first.version not in first.skill_root
    assert first.workspace_key != second.workspace_key
    assert not (workspace / ".k41-agent" / "skills").exists()
    assert json.loads(manifest.read_text())["version"] == first.version


@pytest.mark.asyncio
async def test_source_mode_reads_origin_without_snapshot_and_requires_explicit_refresh(execution, monkeypatch, isolated_container):
    source, workspace, service = execution
    configure(monkeypatch, isolated_container, **{"skills.local_execution_mode": "source"})
    loaded = await service.activate("demo", workspace=str(workspace), thread_id="one", agent_name="default")
    assert loaded.execution_mode == "source"
    assert native_path(Path(loaded.skill_root)) == native_path(source)
    assert not service.cache_root.exists() and not service.snapshots.root.exists()
    (source / "asset.bin").write_bytes(b"changed")
    with pytest.raises(ValueError, match="/skill refresh demo"):
        await service.restore(asdict(loaded), workspace=str(workspace), thread_id="one")
    options = {"workspace": str(workspace), "thread_id": "one", "agent_name": "default", "names": None,
               "tools": [type("Tool", (), {"name": "skill"})()]}
    state = {"active_skills": {activation_key("default", str(workspace), "demo"): asdict(loaded)},
             "messages": [HumanMessage(content="/skill refresh demo")]}
    _, active, _ = await active_context(state, **options)
    assert any(entry["version"] != loaded.version for entry in active.values())
    assert not service.cache_root.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("manifest", ["pyproject.toml", "package.json", "pnpm-workspace.yaml"])
async def test_source_mode_uses_snapshot_when_dependencies_need_package_mutation(execution, monkeypatch, isolated_container, manifest):
    source, workspace, service = execution
    configure(monkeypatch, isolated_container, **{"skills.local_execution_mode": "source"})
    (source / manifest).write_text("{}" if manifest.endswith("json") else "")
    loaded = await service.activate("demo", workspace=str(workspace), thread_id="one", agent_name="default")
    assert loaded.execution_mode == "snapshot"
    assert native_path(Path(loaded.skill_root)) != native_path(source)


@pytest.mark.asyncio
async def test_legacy_checkpoint_migrates_to_short_location_with_original_bytes(execution):
    source, workspace, service = execution
    loaded = await service.activate("demo", workspace=str(workspace), thread_id="one", agent_name="default")
    legacy = asdict(loaded)
    old_root = str(workspace / ".k41-agent" / "skills" / ("a" * 24) / loaded.id / loaded.version)
    legacy["skill_root"] = old_root
    legacy["content"] = loaded.content.replace(loaded.skill_root, old_root)
    for key in ("execution_mode", "package_path", "environment_variable", "shell"):
        legacy.pop(key)
    (source / "asset.bin").write_bytes(b"changed origin")
    restored = await service.restore(legacy, workspace=str(workspace), thread_id="one")
    assert restored["skill_root"] == loaded.skill_root
    assert old_root not in restored["content"]
    assert (Path(restored["skill_root"]) / "asset.bin").read_bytes() == b"\x00\xff"
    assert restored["environment_variable"] == "K41_SKILL_DEMO"


@pytest.mark.asyncio
async def test_shared_resources_are_scoped_read_only_and_do_not_override_read_denials(execution):
    source, workspace, service = execution
    loaded = await service.activate("demo", workspace=str(workspace), thread_id="one", agent_name="default")
    permissions = Permissions(OutputStore(workspace.parent / "outputs"))
    context = InvocationContext("default", str(workspace), "one", skill_roots=(loaded.skill_root,), skill_cache_root=str(service.cache_root))
    path = str(Path(loaded.skill_root) / "asset.bin")
    assert permissions.resolve_path(context, path, "read").is_file()
    for action in ("edit", "write"):
        with pytest.raises(CodingError, match="read-only"):
            permissions.resolve_path(context, path, action)
    other = InvocationContext("default", str(workspace), "two", skill_cache_root=str(service.cache_root))
    with pytest.raises(CodingError, match="not active"):
        permissions.resolve_path(other, path, "read")
    denied = InvocationContext("default", str(workspace), "one", skill_roots=(loaded.skill_root,),
        permission_rules=(PermissionRule(action="read", resource="*", effect="deny"),))
    with pytest.raises(CodingError, match="Policy denies"):
        permissions.resolve_path(denied, path, "read")


@pytest.mark.parametrize("engine", ["python", "ripgrep"])
@pytest.mark.parametrize("location", ["workspace", "external"])
@pytest.mark.parametrize("tool_name", ["glob", "grep"])
def test_parent_search_excludes_inactive_skill_resources(tmp_path, monkeypatch, engine, location, tool_name):
    from agent.modules.tools.coding.files import FileService

    if engine == "ripgrep" and not shutil.which("rg"):
        pytest.skip("ripgrep is required")
    if engine == "python":
        monkeypatch.setattr("agent.modules.tools.coding.files.shutil.which", lambda name: None)
    workspace = tmp_path / "workspace"
    parent = workspace / ".k41-agent" if location == "workspace" else tmp_path / "external"
    cache = parent / "s"
    active = cache / "sk-active"
    inactive = cache / "sk-inactive"
    active.mkdir(parents=True)
    inactive.mkdir()
    (parent / "public.md").write_text("VISIBLE public text", encoding="utf-8")
    (active / "guide.md").write_text("VISIBLE active instructions", encoding="utf-8")
    private = inactive / "private.md"
    private.write_text("SECRET inactive instructions", encoding="utf-8")
    workspace.mkdir(exist_ok=True)
    context = InvocationContext("default", str(workspace), "one", skill_roots=(str(active),),
        skill_cache_root=str(cache), permission_rules=(PermissionRule(action="external_directory", resource="*", effect="allow"),))
    files = FileService(Permissions(OutputStore(tmp_path / "outputs")))
    with pytest.raises(CodingError, match="not active"):
        files._execute("read", {"file_path": str(private)}, context)
    values = {"path": str(parent), "pattern": "**/*.md" if tool_name == "glob" else "VISIBLE|SECRET"}
    result = files._execute(tool_name, values, context)
    assert "public.md" in result.content
    assert "guide.md" in result.content
    assert "private.md" not in result.content
    assert "SECRET" not in result.content
    assert result.data["engine"] == engine


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["snapshot", "source"])
async def test_actual_graph_script_uses_environment_variable_and_protects_source(execution, mode, monkeypatch, isolated_container):
    if not shutil.which("uv"):
        pytest.skip("uv is required")
    source, workspace, service = execution
    configure(monkeypatch, isolated_container, **{"skills.local_execution_mode": mode})
    original_version = (await service.inventory(str(workspace)))[0].version
    builder = StateGraph(BaseState, context_schema=WorkflowContext)
    builder.add_node("tools", tool_node)
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "one"}}
    context = WorkflowContext(workspace=str(workspace), allowed_tool_names=["skill", "bash", "read"])

    async def call(name, args, identity):
        return await graph.ainvoke({"messages": [AIMessage(content="", tool_calls=[
            {"name": name, "args": args, "id": identity, "type": "tool_call"}])]}, config, context=context)

    activated = await call("skill", {"name": "demo"}, "activate")
    entry = next(iter(activated["active_skills"].values()))
    assert entry["execution_mode"] == mode
    if os.name == "nt":
        assert len(str(Path(entry["skill_root"]) / "scripts" / "read_asset.py")) < 240
    from agent.modules.tools import get_coding_service
    shell = Path(get_coding_service().shell).stem.lower()
    if shell in {"pwsh", "powershell"}:
        command = "uv run --no-project --script (Join-Path $env:K41_SKILL_DEMO 'scripts/read_asset.py') (Join-Path $env:K41_WORKSPACE_ROOT 'result.bin')"
    elif shell == "cmd":
        command = 'uv run --no-project --script "%K41_SKILL_DEMO%\\scripts\\read_asset.py" "%K41_WORKSPACE_ROOT%\\result.bin"'
    else:
        command = 'uv run --no-project --script "$K41_SKILL_DEMO/scripts/read_asset.py" "$K41_WORKSPACE_ROOT/result.bin"'
    result = await call("bash", {"command": command, "workdir": str(workspace), "yield_time_ms": 30000}, "run")
    assert result["messages"][-1].status == "success", result["messages"][-1].content
    assert (workspace / "result.bin").read_bytes() == b"\x00\xff"
    assert not list(Path(entry["skill_root"]).glob(".skill-lease-*"))
    read = await call("read", {"file_path": str(Path(entry["skill_root"]) / "SKILL.md")}, "read")
    assert read["messages"][-1].status == "success", read["messages"][-1].content
    assert (await service.inventory(str(workspace)))[0].version == original_version
    assert not list(source.rglob("__pycache__"))
    if mode == "source":
        assert not service.cache_root.exists() and not service.snapshots.root.exists()


@pytest.mark.asyncio
async def test_running_process_protects_shared_cache_until_it_stops(execution):
    source, workspace, service = execution
    loaded = await service.activate("demo", workspace=str(workspace), thread_id="one", agent_name="default")
    context = InvocationContext("default", str(workspace), "one", skill_roots=(loaded.skill_root,),
                                skill_cache_root=str(service.cache_root))
    from agent.modules.tools import get_coding_service
    shell = get_coding_service().shell
    kind = Path(shell).stem.lower()
    script = "import time; time.sleep(60)"
    if kind in {"pwsh", "powershell"}:
        command = "& '" + sys.executable.replace("'", "''") + "' -c '" + script + "'"
    elif kind == "cmd":
        command = f'"{sys.executable}" -c "{script}"'
    else:
        command = shlex.quote(sys.executable) + " -c " + shlex.quote(script)
    manager = ProcessManager(OutputStore(workspace.parent / "outputs"), register_sessions=False)
    job = await manager.start(context, command, workspace, 60, shell)
    transport = service.execution_transport(str(workspace))
    unrelated = service.cache_root / "user-data"
    unrelated.mkdir()
    try:
        assert len(job.skill_leases) == 1
        aged = time.time() - 8 * 86400
        os.utime(loaded.skill_root, (aged, aged))
        assert not (await transport.operation("cleanup", cache_only=True))["removed"]
        assert Path(loaded.skill_root).is_dir()
    finally:
        await manager.stop(job)
    assert not list(Path(loaded.skill_root).glob(".skill-lease-*"))
    os.utime(loaded.skill_root, (aged, aged))
    assert (await transport.operation("cleanup", cache_only=True))["removed"] == [loaded.package_path]
    assert unrelated.is_dir()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["write_text", "unlink"])
@pytest.mark.parametrize("error_type", [PermissionError, OSError])
async def test_process_runs_when_skill_lease_io_fails(tmp_path, monkeypatch, operation, error_type):
    from agent.modules.tools.coding.processes import resolve_shell

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    conversation_root = native_path(workspace / ".k41-agent" / "skills" / conversation_key("lease-thread"))
    conversation_root.mkdir(parents=True)
    cache = tmp_path / "cache"
    skill_root = cache / "skill"
    skill_root.mkdir(parents=True)
    context = InvocationContext("default", str(workspace), "lease-thread", skill_roots=(str(skill_root),), skill_cache_root=str(cache))
    original = getattr(Path, operation)
    failed = []

    def fail_lease_io(path, *args, **kwargs):
        if path.name.startswith(".skill-lease-"):
            failed.append(path)
            raise error_type(errno.ENOSPC, "Simulated lease I/O failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, fail_lease_io)
    manager = ProcessManager(OutputStore(tmp_path / "outputs"), register_sessions=False)
    try:
        job = await manager.start(context, "echo lease-fallback", workspace, 15, resolve_shell())
        await asyncio.wait_for(job.task, timeout=20)
        result = await manager.observe(job)
        assert job.finished.is_set() and job.process.returncode == 0
        assert result.status == "success" and "lease-fallback" in result.content
        assert len(failed) == 2
        if operation == "write_text":
            assert job.skill_lease is None and job.skill_leases == ()
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_cache_directory_change_rebinds_active_checkpoint(execution, monkeypatch, isolated_container):
    source, workspace, service = execution
    loaded = await service.activate("demo", workspace=str(workspace), thread_id="one", agent_name="default")
    cache = source.parent.parent / "new-cache"
    configure(monkeypatch, isolated_container, **{"skills.cache_root": str(cache)})
    await service.activate("demo", workspace=str(workspace), thread_id="two", agent_name="default")
    restored = await service.restore(asdict(loaded), workspace=str(workspace), thread_id="one")
    assert Path(restored["skill_root"]).parent == cache
    assert loaded.skill_root not in restored["content"]
    assert restored["version"] == loaded.version


@pytest.mark.asyncio
async def test_package_version_queries_cannot_escape_selected_source(execution):
    source, workspace, service = execution
    other = source.parent / "other"
    other.mkdir()
    (other / "SKILL.md").write_text("---\nname: other\ndescription: Other.\n---\n")
    transport = WorkspacePackageIO(str(source.parent), boundary="demo")
    with pytest.raises(ValueError, match="escapes"):
        await transport.operation("versions", "demo", paths=["other"])


@pytest.mark.parametrize("value", [[], {}, "direct", None, True])
def test_invalid_execution_mode_returns_validation_error(value):
    from fastapi import HTTPException
    from agent.delivery.http.dashboard.routes.helpers.settings import normalize_setting_value
    with pytest.raises(HTTPException) as error:
        normalize_setting_value("skills.local_execution_mode", value)
    assert error.value.status_code == 400


@pytest.mark.skipif(os.name != "nt", reason="Windows long path regression")
def test_windows_prefix_accounts_for_deep_children_even_when_root_is_short(tmp_path):
    root = tmp_path / ("r" * max(1, 239 - len(str(tmp_path)) - 1))
    assert len(str(root)) == 239
    result = execution_path(str(root), resources=["scripts/search_library.py"])
    assert result.startswith("\\\\?\\")


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows extended workspace regression")
async def test_extended_workspace_path_does_not_request_external_access(tmp_path, isolated_container):
    workspace = tmp_path / ("r" * max(1, 249 - len(str(tmp_path)) - 1))
    native_path(workspace).mkdir()
    context = InvocationContext("default", str(workspace), "one", permission_rules=(
        PermissionRule(action="external_directory", resource="*", effect="deny"),))
    from agent.modules.tools import get_coding_service
    await get_coding_service().prepare("bash", {"command": "echo ok", "workdir": str(workspace)}, context)


@pytest.mark.parametrize("shell,syntax", [("pwsh.exe", "PowerShell"), ("cmd.exe", "cmd"), ("/bin/bash", "POSIX")])
def test_guidance_uses_actual_shell_and_short_environment_reference(shell, syntax):
    result = shell_guidance(shell, "K41_SKILL_DEMO")
    assert f"Shell syntax: {syntax}" in result
    assert "K41_SKILL_DEMO" in result
    if syntax == "PowerShell":
        assert "Join-Path $env:K41_SKILL_DEMO" in result
        assert "head/export commands are not available" in result
