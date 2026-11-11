"""Auxiliary machines: ownership, target resolution, targeted coding tools and computer tools."""

from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
import io
import json
import os
import shlex
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from agent.modules.instabox import InstaboxService, InstaboxSettings
from agent.modules.tools.result import ToolError, ToolErrorCode
from agent.modules.workspaces import WorkspaceRef
from tests.test_instabox import API_KEY, FakeInstabox, MemoryRepository

ROOT = "/data/home/workspace"


@pytest_asyncio.fixture
async def database(isolated_container):
    from agent.shared.infrastructure.db import Base, load_orm_models
    from agent.shared.infrastructure.db.engine import close_async_engine, initialize_async_engine

    load_orm_models()
    await initialize_async_engine(metadata=Base.metadata, container=isolated_container)
    try:
        yield isolated_container
    finally:
        await close_async_engine(container=isolated_container)


@pytest.fixture
def fake():
    return FakeInstabox()


@pytest_asyncio.fixture
async def instabox(database, fake):
    settings = InstaboxSettings(enabled=True, api_key=API_KEY, max_machines_per_thread=2)
    service = InstaboxService(database, repository=MemoryRepository(), http_client=fake.client(),
                              settings_loader=lambda: settings)
    database._instabox_service = service
    yield service
    await service.close()


def runtime_for(thread_id: str, workspace: WorkspaceRef | None = None):
    workspace = workspace or WorkspaceRef(backend="local", locator=".", metadata={})
    return SimpleNamespace(context={"workspace": workspace, "agent_name": "default"},
                           config={"configurable": {"thread_id": thread_id}},
                           tool_call_id="call-1", state={})


@pytest.fixture
def tools(monkeypatch):
    from agent.modules.tools.builtin import instabox as module

    monkeypatch.setattr(module, "_authorize", lambda *args, **kwargs: None)
    return module


# Ownership ---------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_parallel_reservations_respect_the_limit(database):
    from agent.modules.workspaces import ThreadMachineLimitError, reserve_thread_machine

    results = await asyncio.gather(*(reserve_thread_machine("t1", "instabox", limit=2) for _ in range(4)),
                                   return_exceptions=True)
    assert sum(isinstance(item, int) for item in results) == 2
    assert sum(isinstance(item, ThreadMachineLimitError) for item in results) == 2


@pytest.mark.asyncio
async def test_sub_agents_share_the_root_conversation(database):
    from agent.modules.workspaces import activate_thread_machine, find_thread_resource, reserve_thread_machine

    reservation = await reserve_thread_machine("t1:sub:worker:abc", "instabox", limit=2, root=ROOT)
    await activate_thread_machine(reservation, "m_1")
    assert (await find_thread_resource("t1", "m_1")).thread_id == "t1"
    assert await find_thread_resource("t1:sub:other:def", "m_1") is not None
    assert await find_thread_resource("t2", "m_1") is None


@pytest.mark.asyncio
async def test_released_machines_are_listed_as_unattached(instabox, fake):
    from agent.modules.workspaces import activate_thread_machine, release_thread_resources, reserve_thread_machine
    from agent.modules.workspaces.instabox_backend import list_instabox_cloud_sandboxes

    machine_id = fake.add_machine()
    await activate_thread_machine(await reserve_thread_machine("t1", "instabox", limit=2), machine_id)
    [listed] = await list_instabox_cloud_sandboxes()
    assert listed["is_orphan"] is False and listed["thread_id"] == "t1" and listed["role"] == "auxiliary"

    released = await release_thread_resources("t1")
    assert [record.locator for record in released] == [machine_id]
    [listed] = await list_instabox_cloud_sandboxes()
    assert listed["is_orphan"] is True


# Target resolution -------------------------------------------------------- #


@pytest.mark.asyncio
async def test_resolve_target_refuses_machines_of_other_conversations(database):
    from agent.modules.tools.runtime.targets import resolve_target
    from agent.modules.workspaces import activate_thread_machine, reserve_thread_machine

    await activate_thread_machine(await reserve_thread_machine("t1", "instabox", limit=2, root=ROOT), "m_1", root=ROOT)
    main = await resolve_target(runtime_for("t1"), None)
    assert main.auxiliary is False and main.backend == "local"
    owned = await resolve_target(runtime_for("t1:sub:helper:1"), "m_1")
    assert owned.auxiliary and owned.locator == "m_1" and owned.root == ROOT
    with pytest.raises(ToolError) as refused:
        await resolve_target(runtime_for("t2"), "m_1")
    assert refused.value.code == ToolErrorCode.PERMISSION_DENIED


# Instabox tools ----------------------------------------------------------- #


@pytest.mark.asyncio
async def test_machine_create_records_ownership_and_limit(instabox, fake, tools):
    runtime = runtime_for("t1")
    first = json.loads(await tools._machine_create(runtime, purpose="build"))
    machine_id = first["machine_id"]
    assert first["auxiliary"]["target"] == machine_id
    assert fake.machines[machine_id]["metadata"]["k41_thread_id"] == "t1"
    assert fake.machines[machine_id]["metadata"]["k41_role"] == "auxiliary"

    await tools._machine_create(runtime)
    with pytest.raises(ToolError, match="limit is 2"):
        await tools._machine_create(runtime)

    listed = json.loads(await tools._machine_list(runtime))
    assert {item["role"] for item in listed} == {"auxiliary"} and len(listed) == 2


@pytest.mark.asyncio
async def test_machine_create_releases_the_slot_when_the_provider_fails(instabox, fake, tools):
    import httpx

    fake.fail_next.append(httpx.Response(400, json={"error": {"code": "bad_template", "message": "bad"}}))
    with pytest.raises(ToolError):
        await tools._machine_create(runtime_for("t1"), template="missing")
    from agent.modules.workspaces import list_thread_resources

    assert await list_thread_resources("t1") == []
    await tools._machine_create(runtime_for("t1"))
    await tools._machine_create(runtime_for("t1"))


@pytest.mark.asyncio
async def test_machine_tools_are_scoped_to_the_conversation(instabox, fake, tools):
    workspace_machine = fake.add_machine()
    foreign = fake.add_machine()
    runtime = runtime_for("t1", WorkspaceRef(backend="instabox", locator=workspace_machine, metadata={"root": ROOT}))

    assert json.loads(await tools._machine_get(runtime))["id"] == workspace_machine
    with pytest.raises(ToolError) as refused:
        await tools._machine_get(runtime, foreign)
    assert refused.value.code == ToolErrorCode.PERMISSION_DENIED
    with pytest.raises(ToolError, match="dashboard"):
        await tools._machine_delete(runtime, workspace_machine, workspace_machine)
    with pytest.raises(ToolError) as foreign_delete:
        await tools._machine_delete(runtime, foreign, foreign)
    assert foreign_delete.value.code == ToolErrorCode.PERMISSION_DENIED
    assert foreign in fake.machines and workspace_machine in fake.machines
    assert [item["id"] for item in json.loads(await tools._machine_list(runtime))] == [workspace_machine]


@pytest.mark.asyncio
async def test_stop_and_delete_drop_cached_runtimes(instabox, fake, tools, database, tmp_path):
    from agent.modules.tools.coding.service import CodingService
    from agent.modules.workspaces import find_thread_resource

    runtime = runtime_for("t1")
    machine_id = json.loads(await tools._machine_create(runtime))["machine_id"]
    coding = CodingService(tmp_path / "coding")
    database._coding_service = coding
    coding.remote.clients[("instabox", machine_id, ROOT)] = object()
    instabox.desktop._ready.add(machine_id)

    await tools._action_tool("stop")(runtime, machine_id)
    assert ("instabox", machine_id, ROOT) not in coding.remote.clients
    assert machine_id not in instabox.desktop._ready

    await tools._machine_delete(runtime, machine_id, machine_id)
    assert machine_id not in fake.machines
    assert await find_thread_resource("t1", machine_id) is None


@pytest.mark.asyncio
async def test_snapshots_are_scoped_to_the_conversation(instabox, fake, tools):
    runtime = runtime_for("t1")
    machine_id = json.loads(await tools._machine_create(runtime))["machine_id"]
    fake.snapshots["sn_foreign"] = {"id": "sn_foreign", "name": "other", "state": "ready"}
    with pytest.raises(ToolError) as refused:
        await tools._snapshot_get(runtime, "sn_foreign")
    assert refused.value.code == ToolErrorCode.PERMISSION_DENIED
    with pytest.raises(ToolError):
        await tools._machine_create(runtime, snapshot_id="sn_foreign")
    assert json.loads(await tools._snapshot_list(runtime)) == []
    with pytest.raises(ToolError):
        await tools._snapshot_restore(runtime, machine_id, "sn_foreign")


@pytest.mark.asyncio
async def test_public_ports_ask_separately(instabox, fake, monkeypatch):
    from agent.modules.tools.builtin import instabox as module

    checked = []
    monkeypatch.setattr(module, "_authorize", lambda runtime, name, resource, values: checked.append(name))
    machine_id = fake.add_machine()
    runtime = runtime_for("t1", WorkspaceRef(backend="instabox", locator=machine_id, metadata={"root": ROOT}))
    await module._port_publish(runtime, 8080)
    assert checked == ["instabox_port_publish"]
    await module._port_publish(runtime, 8081, access="public")
    assert checked[-2:] == ["instabox_port_publish", "instabox_port_public"]


def test_new_actions_ask_by_default(tmp_path):
    from agent.modules.tools.coding.contracts import CodingError, InvocationContext
    from agent.modules.tools.coding.permissions import Permissions

    permissions = Permissions(SimpleNamespace(owner_dir=lambda context: tmp_path))
    context = InvocationContext(agent_name="a", workspace="/w", thread_id="t")
    for action in ("instabox_machine_create", "instabox_machine_fork", "instabox_port_public", "computer_open_url"):
        with pytest.raises(CodingError):
            permissions.assert_allowed(context, action, "m_1", allow_interrupt=False)
    permissions.assert_allowed(context, "computer_click", "m_1", allow_interrupt=False)


# Targeted coding tools ---------------------------------------------------- #


@pytest.mark.asyncio
async def test_auxiliary_invocation_skips_main_workspace_logic(tmp_path, isolated_container, monkeypatch):
    from agent.modules.tools.coding.adapter import SCHEMAS
    from agent.modules.tools.coding.models import InvocationContext, ToolDefinition
    from agent.modules.tools.coding.remote_worker import SandboxWorker
    from agent.modules.tools.coding.service import CodingService

    workspace = tmp_path / "auxiliary-machine"
    workspace.mkdir()
    worker = SandboxWorker(tmp_path / "remote-output", str(workspace))
    service = CodingService(tmp_path / "application-output")
    isolated_container._coding_service = service
    context = InvocationContext("default", str(workspace), "t1", backend="instabox", locator="m_aux",
                                tool_call_id="call-1", auxiliary=True)

    class Client:
        endpoint = {"runtime_dir": "runtime"}
        backend = SimpleNamespace(ref=WorkspaceRef(backend="instabox", locator="m_aux"), root=str(workspace))

        async def ensure_started(self):
            pass

        async def call(self, payload):
            return await worker.dispatch(payload)

    service.remote.clients[("instabox", "m_aux", str(workspace))] = Client()

    async def forbidden(*args, **kwargs):
        raise AssertionError("main workspace persistence must not run for auxiliary machines")

    monkeypatch.setattr(service, "_persist_thread_result", forbidden)
    from agent.modules import projects

    monkeypatch.setattr(projects, "get_project_workspace_service", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("project bindings must not be consulted")))

    async def execute(parsed, ctx, runtime):
        return await runtime.execute("write", parsed.model_dump(), ctx)

    result = await service.invoke(ToolDefinition("write", "write", SCHEMAS["write"], execute),
                                  {"path": "out.txt", "content": "hello"}, context)
    assert result.status == "success", result.content
    assert (workspace / "out.txt").read_text() == "hello"


def test_apply_target_clears_project_and_skill_state():
    from agent.modules.tools.coding.adapter import apply_target
    from agent.modules.tools.coding.models import InvocationContext
    from agent.modules.tools.runtime.targets import ExecutionTarget

    context = InvocationContext("a", "/main", "t", backend="daytona", locator="s1", project_id=3,
                                project_workspace_id=4, runtime_generation=5, skill_roots=("/skill",),
                                skill_environment=(("K41_SKILL_ROOT", "/skill"),))
    aux = apply_target(context, ExecutionTarget("instabox", "m_1", ROOT, True))
    assert (aux.backend, aux.locator, aux.workspace, aux.auxiliary) == ("instabox", "m_1", ROOT, True)
    assert aux.project_id is None and aux.project_workspace_id is None and aux.skill_roots == ()
    assert aux.owner != context.owner
    assert apply_target(context, ExecutionTarget("daytona", "s1", "/main", False)) is context


def test_targeted_schema_keeps_journal_schema():
    from agent.modules.tools.builtin.coding_tools import bash, targeted_tool

    targeted = targeted_tool("bash")
    assert "target" in targeted.tool_call_schema.model_json_schema()["properties"]
    assert "target" not in bash.tool_call_schema.model_json_schema()["properties"]
    assert targeted.coding_definition.input_schema is bash.coding_definition.input_schema
    assert targeted_tool("computer_action") is None


@pytest.mark.asyncio
async def test_batch_preparation_routes_target_to_the_machine(database, monkeypatch):
    from agent.modules.tools.builtin.coding_tools import read, targeted_tool
    from agent.modules.tools.coding import adapter
    from agent.modules.workspaces import activate_thread_machine, reserve_thread_machine

    await activate_thread_machine(await reserve_thread_machine("t1", "instabox", limit=2, root=ROOT), "m_1", root=ROOT)
    captured = []

    from agent.modules.tools.coding.service import CodingService

    class Service:
        failure = staticmethod(CodingService.failure)

        async def prepare_batch(self, invocations):
            captured.extend(invocations)

    monkeypatch.setattr(adapter, "get_coding_service", lambda: Service())
    runtime = runtime_for("t1")
    calls = [{"name": "read", "id": "a", "args": {"path": "x", "target": "m_1"}},
             {"name": "read", "id": "b", "args": {"path": "y"}}]
    await adapter.prepare_tool_batch([targeted_tool("read")], calls, runtime)
    assert [(item.context.locator, item.context.auxiliary, item.args) for item in captured] == [
        ("m_1", True, {"path": "x"}), (str(Path(".").resolve()), False, {"path": "y"})]

    # An agent without auxiliary machines cannot smuggle a target through the base tool.
    captured.clear()
    calls = [{"name": "read", "id": "c", "args": {"path": "x", "target": "m_1"}}]
    await adapter.prepare_tool_batch([read], calls, runtime)
    assert captured[0].args == {"path": "x", "target": "m_1"} and captured[0].context.auxiliary is False

    captured.clear()
    calls = [{"name": "read", "id": "d", "args": {"path": "x", "target": "m_other"}}]
    failure = await adapter.prepare_tool_batch([targeted_tool("read")], calls, runtime)
    assert failure is not None and failure.error.code == "permission_denied" and not captured


def test_resolver_adds_target_only_with_auxiliary_machine_tools(isolated_container, monkeypatch):
    from agent.modules.tools.resolver import ToolResolver

    def resolve(tools):
        config = SimpleNamespace(name="a", tools=tools, sub_agents=None, tool_configs={})
        monkeypatch.setattr(ToolResolver, "_load_agent_config", staticmethod(lambda name: config))
        resolved = {tool.name: tool for tool in ToolResolver(include_mcp=False).resolve_for_agent("a")}
        return "target" in resolved["bash"].tool_call_schema.model_json_schema()["properties"]

    assert resolve(["bash", "read"]) is False
    assert resolve(["bash", "read", "instabox_machine_get"]) is True
    assert resolve(["bash", "copy_files"]) is True


@pytest.mark.asyncio
async def test_fallback_reader_targets_auxiliary_outputs_without_allowing_other_files(database, tmp_path, monkeypatch):
    from agent.modules.tools import get_coding_service
    from agent.modules.tools.coding.models import ToolResult
    from agent.modules.tools.resolver import ToolResolver
    from agent.modules.workspaces import record_thread_resource

    config = SimpleNamespace(name="a", tools=["bash", "instabox_machine_create"], sub_agents=None, tool_configs={})
    monkeypatch.setattr(ToolResolver, "_load_agent_config", staticmethod(lambda name: config))
    workspace = WorkspaceRef(backend="local", locator=str(tmp_path))
    resolver = ToolResolver(include_mcp=False)
    resolved = resolver.for_workspace(resolver.resolve_for_agent("a"), workspace)
    reader = next(tool for tool in resolved if tool.name == "read")
    assert "target" in reader.tool_call_schema.model_json_schema()["properties"]

    await record_thread_resource("t1", "machine", "instabox", "m_aux", root=ROOT)
    coding = get_coding_service()
    monkeypatch.setattr(coding.remote, "prepare", AsyncMock())
    execute = AsyncMock(return_value=ToolResult(content="retained output"))
    monkeypatch.setattr(coding.remote, "execute", execute)
    runtime = runtime_for("t1", workspace)
    result = await reader.coroutine(runtime, path=".k41-agent/outputs/log.txt", target="m_aux")
    assert result.content == "retained output"
    context = execute.await_args.args[2]
    assert context.auxiliary and context.locator == "m_aux" and context.workspace == ROOT

    denied = await reader.coroutine(runtime, path="source.py", target="m_aux")
    assert denied.status == "error" and "only read retained tool output" in denied.content
    assert execute.await_count == 1


# Computer tools ----------------------------------------------------------- #


def test_legacy_desktop_tool_names_map_to_computer_tools():
    from agent.modules.tools.coding.names import canonical_tool_names

    assert canonical_tool_names(["instabox_desktop_click", "instabox_desktop_type", "instabox_desktop_screenshot"]) == [
        "computer_action", "computer_screenshot"]


@pytest.mark.asyncio
async def test_computer_action_validates_fields_per_action(isolated_container):
    from agent.modules.tools.builtin.computer import _computer_action

    runtime = runtime_for("t1")
    with pytest.raises(ToolError, match="Step 1: Action click requires: x, y"):
        await _computer_action(runtime, [{"action": "click"}])
    with pytest.raises(ToolError, match="Step 2: .*x2, y2"):
        await _computer_action(runtime, [{"action": "move", "x": 1, "y": 1}, {"action": "drag", "x": 1, "y": 2}])
    with pytest.raises(ToolError, match="both x and y"):
        await _computer_action(runtime, [{"action": "scroll", "x": 3}])
    with pytest.raises(ToolError, match="http"):
        await _computer_action(runtime, [{"action": "open_url", "url": "file:///etc/passwd"}])


@pytest.mark.asyncio
async def test_computer_tools_refuse_backends_without_desktop(isolated_container):
    from agent.modules.tools.builtin.computer import _computer_screenshot

    with pytest.raises(ToolError, match="has no desktop") as refused:
        await _computer_screenshot(runtime_for("t1"))
    assert refused.value.code == ToolErrorCode.INVALID_INPUT


@pytest.mark.asyncio
async def test_instabox_computer_translates_actions(instabox, fake):
    from agent.modules.workspaces import ComputerStep, get_workspace_computer

    machine_id = fake.add_machine()
    computer = await get_workspace_computer("instabox")
    await computer.act(machine_id, ComputerStep.parse({"action": "drag", "x": 0, "y": 0, "x2": 12, "y2": 24}))
    command = fake.exec_calls[-1]["cmd"]
    assert "xdotool mousemove 0 0 mousedown 1 sleep 0.05 mousemove 1 2 sleep 0.02" in command
    assert command.endswith("mousemove 12 24 sleep 0.02 mouseup 1")
    await computer.act(machine_id, ComputerStep.parse({"action": "double_click", "x": 5, "y": 6}))
    assert fake.exec_calls[-1]["cmd"].endswith("xdotool mousemove 5 6 click --repeat 2 1")
    await computer.act(machine_id, ComputerStep.parse({"action": "key", "keys": ["Control", "L"]}))
    assert fake.exec_calls[-1]["cmd"].endswith("xdotool key -- ctrl+l")
    await computer.act(machine_id, ComputerStep.parse({"action": "key", "keys": ["return"]}))
    assert fake.exec_calls[-1]["cmd"].endswith("xdotool key -- Return")


# Copy --------------------------------------------------------------------- #


def test_copy_paths_stay_inside_the_root():
    from agent.modules.tools.builtin.copy_files import _relative

    assert _relative("./src//app.py", "p") == "src/app.py"
    assert _relative(".", "p") == "."
    for value in ("/etc/passwd", "../x", "C:/x", ""):
        with pytest.raises(ToolError):
            _relative(value, "p")


def test_local_directory_archive_round_trip_skips_internal_files(tmp_path):
    from agent.modules.tools.builtin.copy_files import _members, _read_local

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / ".env").write_text("A=1")
    (tmp_path / "src" / "nested").mkdir()
    (tmp_path / "src" / "nested" / "b.txt").write_text("b")
    (tmp_path / "src" / ".k41-agent").mkdir()
    (tmp_path / "src" / ".k41-agent" / "secret").write_text("x")
    kind, data = _read_local(tmp_path / "src")
    assert kind == "directory"
    assert sorted(_members(data)) == [(".env", b"A=1"), ("nested/b.txt", b"b")]


def test_archives_with_escaping_entries_are_refused():
    from agent.modules.tools.builtin.copy_files import _members

    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        info = tarfile.TarInfo("../evil")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ToolError, match="escapes"):
        _members(output.getvalue())


@pytest.mark.asyncio
async def test_copy_from_auxiliary_machine_into_local_workspace(database, tmp_path, monkeypatch):
    from agent.modules.tools.builtin import copy_files as module
    from agent.modules.workspaces import activate_thread_machine, reserve_thread_machine

    monkeypatch.setattr(module, "_authorize", lambda *args: None)
    await activate_thread_machine(await reserve_thread_machine("t1", "instabox", limit=2, root=ROOT), "m_1", root=ROOT)
    main = tmp_path / "main"
    main.mkdir()

    async def read(side):
        assert side.target.auxiliary and side.absolute == f"{ROOT}/dist"
        return "file", b"artifact"

    monkeypatch.setattr(module, "_read", read)
    resolved_paths = module._resolved_paths

    async def resolve_paths(side, paths):
        if side.target.auxiliary:
            return [f"{ROOT}/{path}" for path in paths]
        return await resolved_paths(side, paths)

    monkeypatch.setattr(module, "_resolved_paths", resolve_paths)
    runtime = runtime_for("t1", WorkspaceRef(backend="local", locator=str(main), metadata={}))
    result = json.loads(await module._copy_files(runtime, "dist", "out/app.bin", source_target="m_1"))
    assert result == {"copied": "file", "files": 1, "bytes": 8, "source": "m_1:dist", "destination": "workspace:out/app.bin"}
    assert (main / "out" / "app.bin").read_bytes() == b"artifact"
    with pytest.raises(ToolError) as refused:
        await module._copy_files(runtime, "dist", "x", source_target="m_foreign")
    assert refused.value.code == ToolErrorCode.PERMISSION_DENIED


def _directory_link(target: Path, link: Path) -> None:
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def _permission_context(monkeypatch, workspace: Path, *rules):
    from agent.modules.tools.coding.models import InvocationContext
    from agent.modules.tools.coding.permissions import Permissions
    from agent.modules.tools.coding.storage import OutputStore

    context = InvocationContext("default", str(workspace), "t1", permission_rules=rules)
    permissions = Permissions(OutputStore(workspace.parent / "permissions"))
    monkeypatch.setattr("agent.modules.tools.coding.adapter.invocation_context", lambda runtime: context)
    monkeypatch.setattr("agent.modules.tools.coding.service.get_coding_service",
                        lambda: SimpleNamespace(permissions=permissions))


@pytest.mark.asyncio
async def test_copy_refuses_source_links_outside_workspace(isolated_container, tmp_path):
    from agent.modules.tools.builtin.copy_files import _copy_files

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("private")
    _directory_link(outside, workspace / "escape")
    runtime = runtime_for("t1", WorkspaceRef(backend="local", locator=str(workspace)))

    with pytest.raises(ToolError) as refused:
        await _copy_files(runtime, "escape/private.txt", "out.txt")
    assert refused.value.code == ToolErrorCode.PERMISSION_DENIED
    assert not (workspace / "out.txt").exists()


def test_local_directory_copy_skips_nested_links(tmp_path):
    from agent.modules.tools.builtin.copy_files import _members, _read_local

    source = tmp_path / "source"
    source.mkdir()
    (source / "ok.txt").write_text("ok")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("private")
    _directory_link(outside, source / "escape")
    _, data = _read_local(source)
    assert _members(data) == [("ok.txt", b"ok")]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["read", "edit"])
async def test_directory_copy_checks_child_permissions_before_writing(isolated_container, tmp_path, monkeypatch, action):
    from agent.modules.tools.builtin.copy_files import _copy_files
    from agent.modules.tools.coding.models import PermissionRule

    workspace = tmp_path / "workspace"
    (workspace / "source").mkdir(parents=True)
    (workspace / "source" / "ok.txt").write_text("ok")
    (workspace / "source" / "private.txt").write_text("private")
    denied = workspace / ("source" if action == "read" else "destination") / "private.txt"
    _permission_context(monkeypatch, workspace, PermissionRule(action=action, resource=str(denied), effect="deny"))
    runtime = runtime_for("t1", WorkspaceRef(backend="local", locator=str(workspace)))

    with pytest.raises(ToolError) as refused:
        await _copy_files(runtime, "source", "destination")
    assert refused.value.code == ToolErrorCode.PERMISSION_DENIED
    assert not (workspace / "destination").exists()


@pytest.mark.asyncio
async def test_copy_checks_all_destination_links_before_writing(isolated_container, tmp_path):
    from agent.modules.tools.builtin.copy_files import _copy_files

    workspace = tmp_path / "workspace"
    (workspace / "source" / "nested").mkdir(parents=True)
    (workspace / "source" / "ok.txt").write_text("ok")
    (workspace / "source" / "nested" / "private.txt").write_text("new")
    (workspace / "destination").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("original")
    _directory_link(outside, workspace / "destination" / "nested")
    runtime = runtime_for("t1", WorkspaceRef(backend="local", locator=str(workspace)))

    with pytest.raises(ToolError) as refused:
        await _copy_files(runtime, "source", "destination")
    assert refused.value.code == ToolErrorCode.PERMISSION_DENIED
    assert (outside / "private.txt").read_text() == "original"
    assert not (workspace / "destination" / "ok.txt").exists()


@pytest.mark.asyncio
async def test_remote_copy_refuses_source_links_outside_root(tmp_path, monkeypatch):
    from agent.modules.tools.builtin import copy_files as module
    from agent.modules.tools.runtime.targets import ExecutionTarget
    from agent.modules.workspaces import CommandResult

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("private")
    _directory_link(outside, workspace / "escape")

    async def execute(command, **kwargs):
        executable, flag, script, root, paths = shlex.split(command)
        assert (executable, flag) == ("python3", "-c")
        output = io.StringIO()
        with monkeypatch.context() as patch:
            patch.setattr(sys, "argv", ["copy-paths", root, paths])
            with redirect_stdout(output):
                exec(script, {})
        return CommandResult(output=output.getvalue(), exit_code=0)

    backend = SimpleNamespace(root=str(workspace), execute_coding_command=execute, download_coding_file=AsyncMock())
    monkeypatch.setattr(module._Side, "raw", AsyncMock(return_value=backend))
    side = module._Side(ExecutionTarget("instabox", "m_1", str(workspace), True), "escape/private.txt", None)
    with pytest.raises(ToolError) as refused:
        await module._read(side)
    assert refused.value.code == ToolErrorCode.PERMISSION_DENIED
    backend.download_coding_file.assert_not_awaited()


def test_remote_archive_preserves_regular_file_mode_and_removes_links():
    from agent.modules.tools.builtin.copy_files import _members, _regular_archive

    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        script = tarfile.TarInfo("./run.sh")
        script.size, script.mode = 4, 0o755
        archive.addfile(script, io.BytesIO(b"exit"))
        link = tarfile.TarInfo("escape")
        link.type, link.linkname = tarfile.SYMTYPE, "/outside"
        archive.addfile(link)
        private = tarfile.TarInfo("nested/.k41-agent/private.txt")
        private.size = 7
        archive.addfile(private, io.BytesIO(b"private"))
    payload = output.getvalue()
    safe = _regular_archive(payload, _members(payload))
    with tarfile.open(fileobj=io.BytesIO(safe), mode="r:gz") as archive:
        assert archive.getnames() == ["run.sh"]
        assert archive.getmember("run.sh").mode == 0o755
        assert archive.extractfile("run.sh").read() == b"exit"


def test_directory_copy_limits_expanded_bytes(monkeypatch):
    from agent.modules.tools.builtin import copy_files as module

    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        entry = tarfile.TarInfo("large.txt")
        entry.size = 2048
        archive.addfile(entry, io.BytesIO(b"0" * entry.size))
    monkeypatch.setattr(module, "MAX_COPY_BYTES", 1024)
    assert len(output.getvalue()) < module.MAX_COPY_BYTES
    with pytest.raises(ToolError, match="limit is 1024"):
        module._members(output.getvalue())


@pytest.mark.asyncio
async def test_local_directory_copy_succeeds_with_allowed_files(isolated_container, tmp_path):
    from agent.modules.tools.builtin.copy_files import _copy_files

    workspace = tmp_path / "workspace"
    (workspace / "source" / "nested").mkdir(parents=True)
    (workspace / "source" / "one.txt").write_text("one")
    (workspace / "source" / "nested" / "two.txt").write_text("two")
    runtime = runtime_for("t1", WorkspaceRef(backend="local", locator=str(workspace)))
    result = json.loads(await _copy_files(runtime, "source", "destination"))
    assert result["files"] == 2
    assert (workspace / "destination" / "one.txt").read_text() == "one"
    assert (workspace / "destination" / "nested" / "two.txt").read_text() == "two"


@pytest.mark.asyncio
async def test_computer_action_authorizes_screenshot_before_input(isolated_container, tmp_path, monkeypatch):
    from agent.modules.tools.builtin import computer as module
    from agent.modules.tools.coding.models import PermissionRule
    from agent.modules.tools.runtime.targets import ExecutionTarget

    _permission_context(monkeypatch, tmp_path, PermissionRule(action="computer_screenshot", effect="deny"))
    from agent.modules.workspaces.computer import ComputerScreenshot

    computer = SimpleNamespace(act=AsyncMock(return_value={"ok": True}),
                               screenshot=AsyncMock(return_value=ComputerScreenshot(b"", 1280, 800)))
    target = ExecutionTarget("instabox", "m_1", ROOT, True)
    monkeypatch.setattr(module, "_computer", AsyncMock(return_value=(target, computer)))
    runtime = runtime_for("t1")
    with pytest.raises(ToolError) as refused:
        await module._computer_action(runtime, [{"action": "click", "x": 1, "y": 2}])
    assert refused.value.code == ToolErrorCode.PERMISSION_DENIED
    computer.act.assert_not_awaited()
    computer.screenshot.assert_not_awaited()
    result = await module._computer_action(runtime, [{"action": "click", "x": 1, "y": 2}], screenshot=False)
    assert json.loads(result)["ok"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["create", "fork"])
@pytest.mark.parametrize("status", ["succeeded", "failed"])
async def test_cancelled_machine_creation_settles_ownership(instabox, fake, tools, monkeypatch, kind, status):
    from agent.modules.workspaces import ThreadMachineLimitError, list_thread_resources, reserve_thread_machine

    watching, finish = asyncio.Event(), asyncio.Event()
    original = instabox._watch_operation

    async def delayed_watch(key):
        watching.set()
        await finish.wait()
        return await original(key)

    monkeypatch.setattr(instabox, "_watch_operation", delayed_watch)
    source_id = fake.add_machine()
    runtime = runtime_for("t1", WorkspaceRef(backend="instabox", locator=source_id, metadata={"root": ROOT}))
    creation = asyncio.create_task(tools._machine_create(runtime) if kind == "create" else tools._machine_fork(runtime))
    await asyncio.wait_for(watching.wait(), 5)
    watcher = next(iter(instabox._watchers.values()))
    try:
        creation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await creation
        with pytest.raises(ThreadMachineLimitError):
            await reserve_thread_machine("t1", "instabox", limit=1)
        operation = next(iter(fake.operations.values()))
        if status == "failed":
            operation.update(state="failed", error={"code": "create_failed"})
        finish.set()
        result = await watcher
        assert result["status"] == status
        resources = await list_thread_resources("t1")
        if status == "succeeded":
            assert [record.locator for record in resources] == [result["machine_id"]]
            assert result["machine_id"] in fake.machines
        else:
            assert resources == []
            await reserve_thread_machine("t1", "instabox", limit=1)
    finally:
        finish.set()
        await asyncio.gather(creation, watcher, return_exceptions=True)


@pytest.mark.asyncio
async def test_machine_creation_timeout_keeps_reservation_until_completion(instabox, fake, monkeypatch):
    from agent.modules.workspaces import ThreadMachineLimitError, find_thread_resource, reserve_thread_machine

    finish = asyncio.Event()
    original = instabox._watch_operation

    async def delayed_watch(key):
        await finish.wait()
        return await original(key)

    monkeypatch.setattr(instabox, "_watch_operation", delayed_watch)
    reservation = await reserve_thread_machine("t1", "instabox", limit=1, root=ROOT)
    try:
        with pytest.raises(TimeoutError):
            await instabox.submit(kind="create", method="POST", path="/v1/machines", body={},
                                  auxiliary_reservation_id=reservation, wait=True, timeout=0.01)
        watcher = next(iter(instabox._watchers.values()))
        with pytest.raises(ThreadMachineLimitError):
            await reserve_thread_machine("t1", "instabox", limit=1)
        finish.set()
        result = await watcher
        assert await find_thread_resource("t1", result["machine_id"]) is not None
    finally:
        finish.set()
        await asyncio.gather(*instabox._watchers.values(), return_exceptions=True)


@pytest.mark.asyncio
async def test_resumed_machine_creation_retains_ownership(instabox, fake, database, monkeypatch):
    from agent.modules.workspaces import find_thread_resource, reserve_thread_machine

    original = instabox._watch_operation
    blocked = asyncio.Event()

    async def delayed_watch(key):
        await blocked.wait()
        return await original(key)

    monkeypatch.setattr(instabox, "_watch_operation", delayed_watch)
    reservation = await reserve_thread_machine("t1", "instabox", limit=1, root=ROOT)
    record = await instabox.create_machine(auxiliary_reservation_id=reservation)
    watcher = next(iter(instabox._watchers.values()))
    watcher.cancel()
    await asyncio.gather(watcher, return_exceptions=True)
    resumed = InstaboxService(database, repository=instabox.repository, http_client=fake.client(),
                              settings_loader=instabox.settings)
    try:
        await resumed.resume_pending_operations()
        [result] = await asyncio.gather(*resumed._watchers.values())
        owned = await find_thread_resource("t1", result["machine_id"])
        assert owned is not None and owned.id == reservation
        assert result["idempotency_key"] == record["idempotency_key"]
        assert len(fake.machines) == 1
    finally:
        await resumed.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["succeeded", "failed"])
async def test_dashboard_delete_updates_ownership_only_after_success(instabox, fake, database, monkeypatch, status):
    from agent.delivery.http.dashboard.routes import instabox as routes
    from agent.modules.workspaces import find_thread_resource, record_thread_resource

    machine_id = fake.add_machine()
    await record_thread_resource("t1", "machine", "instabox", machine_id)
    cached = object()
    clients = {("instabox", machine_id, ROOT): cached}
    from agent.modules.tools.coding.remote import RemoteCodingRuntime

    remote = SimpleNamespace(clients=clients)
    remote.forget = lambda backend, locator: RemoteCodingRuntime.forget(remote, backend, locator)
    database._coding_service = SimpleNamespace(remote=remote)
    instabox.desktop._ready.add(machine_id)
    await instabox.repository.save_port_token(machine_id, 8080, "private-token", "https://preview.example/")
    watching, finish = asyncio.Event(), asyncio.Event()
    original = instabox._watch_operation

    async def delayed_watch(key):
        watching.set()
        await finish.wait()
        return await original(key)

    monkeypatch.setattr(instabox, "_watch_operation", delayed_watch)
    record = await routes.delete_machine(machine_id, routes.ConfirmBody(confirm=machine_id), admin="alice", ibx=instabox)
    await asyncio.wait_for(watching.wait(), 5)
    watcher = next(iter(instabox._watchers.values()))
    try:
        assert record["status"] == "queued"
        assert await find_thread_resource("t1", machine_id) is not None
        assert clients[("instabox", machine_id, ROOT)] is cached
        assert machine_id in instabox.desktop._ready
        assert await instabox.repository.port_token(machine_id, 8080) is not None
        if status == "failed":
            next(iter(fake.operations.values())).update(state="failed", error={"code": "delete_failed"})
        finish.set()
        assert (await watcher)["status"] == status
        assert (await find_thread_resource("t1", machine_id) is None) == (status == "succeeded")
        assert (machine_id not in fake.machines) == (status == "succeeded")
        assert (not clients) == (status == "succeeded")
        assert (machine_id not in instabox.desktop._ready) == (status == "succeeded")
        assert (await instabox.repository.port_token(machine_id, 8080) is None) == (status == "succeeded")
    finally:
        finish.set()
        await asyncio.gather(watcher, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_sandbox_delete_preserves_auxiliary_ownership(instabox, fake):
    from agent.modules.workspaces import find_thread_resource, record_thread_resource
    from agent.modules.workspaces.instabox_backend import delete_instabox_workspace

    machine_id = fake.add_machine()
    await record_thread_resource("t1", "machine", "instabox", machine_id)

    def fail_delete(request):
        if request.url.path.startswith("/v1/operations/"):
            operation_id = request.url.path.rsplit("/", 1)[-1]
            fake.operations[operation_id].update(state="failed", error={"code": "delete_failed"})

    fake.on_request = fail_delete
    with pytest.raises(RuntimeError, match="deletion failed"):
        await delete_instabox_workspace(WorkspaceRef(backend="instabox", locator=machine_id))
    assert await find_thread_resource("t1", machine_id) is not None
    assert machine_id in fake.machines


@pytest.mark.asyncio
@pytest.mark.parametrize("released", [False, True])
async def test_restore_checks_processes_at_auxiliary_root(instabox, fake, monkeypatch, released):
    from agent.modules.instabox import InstaboxConflictError
    from agent.modules.workspaces import record_thread_resource, release_thread_resources

    machine_id = fake.add_machine()
    root = "/custom/repository"
    await record_thread_resource("t1", "machine", "instabox", machine_id, root=root)
    if released:
        await release_thread_resources("t1")
    fake.snapshots["sn_backup"] = {"id": "sn_backup", "state": "ready"}

    async def workspace_processes(ref):
        return int(ref.metadata.get("root") == root)

    probe = AsyncMock(side_effect=workspace_processes)
    monkeypatch.setattr("agent.modules.tools.get_coding_service",
                        lambda: SimpleNamespace(remote=SimpleNamespace(workspace_processes=probe)))
    with pytest.raises(InstaboxConflictError) as refused:
        await instabox.restore_machine(machine_id, "sn_backup", confirm_machine=machine_id, confirm_snapshot="sn_backup")
    assert refused.value.code == "processes_running"
    assert probe.await_args.args[0].metadata["root"] == root
    assert not any(request.url.path.endswith("/restore") for request in fake.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["cancel", "timeout"])
@pytest.mark.parametrize("state", ["ready", "failed"])
async def test_interrupted_snapshot_creation_settles_ownership(instabox, fake, tools, monkeypatch, interruption, state):
    from agent.modules.workspaces import list_thread_resources

    machine_id = fake.add_machine()
    runtime = runtime_for("t1:sub:worker:1", WorkspaceRef(backend="instabox", locator=machine_id, metadata={"root": ROOT}))
    watching, finish = asyncio.Event(), asyncio.Event()
    original_watch = instabox._watch_snapshot

    async def delayed_watch(client, record):
        watching.set()
        await finish.wait()
        return await original_watch(client, record)

    original_submit = instabox.submit

    async def submit(**kwargs):
        return await original_submit(**kwargs, timeout=0.01)

    monkeypatch.setattr(instabox, "_watch_snapshot", delayed_watch)
    if interruption == "timeout":
        monkeypatch.setattr(instabox, "submit", submit)
    creation = asyncio.create_task(tools._snapshot_create(runtime, "backup"))
    await asyncio.wait_for(watching.wait(), 5)
    watcher = next(iter(instabox._watchers.values()))
    try:
        if interruption == "cancel":
            creation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await creation
        else:
            with pytest.raises(ToolError) as timed_out:
                await creation
            assert timed_out.value.code == ToolErrorCode.TIMEOUT
        snapshot_id = next(iter(fake.snapshots))
        fake.snapshots[snapshot_id]["state"] = state
        finish.set()
        await watcher
        resources = await list_thread_resources("t1", kind="snapshot")
        assert [record.locator for record in resources] == ([snapshot_id] if state == "ready" else [])
        if state == "ready":
            assert resources[0].root == ROOT and resources[0].thread_id == "t1"
            assert json.loads(await tools._snapshot_get(runtime, snapshot_id))["state"] == "ready"
    finally:
        finish.set()
        await asyncio.gather(creation, watcher, return_exceptions=True)


@pytest.mark.asyncio
async def test_resumed_snapshot_creation_retains_ownership(instabox, fake, database, tools, monkeypatch):
    from agent.modules.workspaces import list_thread_resources

    machine_id = fake.add_machine()
    runtime = runtime_for("t1", WorkspaceRef(backend="instabox", locator=machine_id, metadata={"root": ROOT}))
    watching = asyncio.Event()

    async def delayed_watch(client, record):
        watching.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(instabox, "_watch_snapshot", delayed_watch)
    creation = asyncio.create_task(tools._snapshot_create(runtime, "backup"))
    await asyncio.wait_for(watching.wait(), 5)
    watcher = next(iter(instabox._watchers.values()))
    creation.cancel()
    watcher.cancel()
    await asyncio.gather(creation, watcher, return_exceptions=True)
    snapshot_id = next(iter(fake.snapshots))
    fake.snapshots[snapshot_id]["state"] = "ready"
    resumed = InstaboxService(database, repository=instabox.repository, http_client=fake.client(),
                              settings_loader=instabox.settings)
    try:
        await resumed.resume_pending_operations()
        await asyncio.gather(*resumed._watchers.values())
        [owned] = await list_thread_resources("t1", kind="snapshot")
        assert owned.locator == snapshot_id and owned.root == ROOT
        assert sum(request.method == "POST" and request.url.path.endswith("/snapshots") for request in fake.requests) == 1
    finally:
        await resumed.close()
