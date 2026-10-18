"""Regression coverage for policy, error status, source loading and batches."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.tools import StructuredTool, ToolException, tool
from langgraph.errors import GraphInterrupt
from pydantic import ValidationError

from agent.modules.tools import ToolCategory, ToolDescriptor, ToolError, ToolErrorCode, ToolPolicy, ToolResolver, ToolSource
from agent.modules.tools.coding.adapter import SCHEMAS, make_coding_tool, prepare_tool_batch
from agent.modules.tools.coding.models import CodingInvocation, InvocationContext, PermissionRule, ToolDefinition, ToolResult
from agent.modules.tools.coding.service import CodingService
from agent.modules.tools.middleware import apply_default_middleware
from agent.modules.tools.registry import ToolRegistry
from agent.modules.tools.registry_service import ToolRegistryService, ensure_mcp_loaded, reload_mcp_descriptors
from agent.modules.tools.sources import BuiltinToolSource, McpToolSource, ToolSourceAdapter
from agent.modules.workflows.run_config import WorkflowContext, make_context


@tool
def probe(value: str) -> str:
    """Return the supplied value."""
    return value


def descriptor(name="read", source=ToolSource.BUILTIN):
    return ToolDescriptor(id=f"{source}.{name}", name=name, description="Probe", source=source,
                          category=ToolCategory.UTILITY, tool=probe)


def test_mcp_names_and_servers_are_both_required():
    policy = ToolPolicy(agent_name="restricted", allowed_tool_names=frozenset({"mcp__github__read"}),
                        allowed_mcp_servers=frozenset({"github"}))
    assert policy.is_allowed(descriptor("mcp__github__read", ToolSource.MCP))
    assert not policy.is_allowed(descriptor("mcp__github__delete", ToolSource.MCP))
    assert not policy.is_allowed(descriptor("mcp__slack__read", ToolSource.MCP))


def test_binding_lookup_failure_denies_mcp(monkeypatch):
    def unavailable(name):
        raise RuntimeError("Bindings unavailable")
    monkeypatch.setattr("agent.modules.mcp.list_agent_mcp_server_names", unavailable)
    policy = ToolPolicy.from_agent_config(SimpleNamespace(name="default", tools=None, sub_agents=None))
    assert policy.is_allowed(descriptor())
    assert not policy.is_allowed(descriptor("mcp__github__read", ToolSource.MCP))


@pytest.mark.parametrize("allowed", [[], ["read"]])
def test_runtime_override_can_only_narrow_policy(monkeypatch, allowed):
    config = SimpleNamespace(name="restricted", tools=["read"], sub_agents=None)
    monkeypatch.setattr(ToolResolver, "_load_agent_config", staticmethod(lambda name: config))
    monkeypatch.setattr("agent.modules.mcp.list_agent_mcp_server_names", lambda name: [])
    service = ToolRegistryService()
    service.load_descriptors([descriptor("read"), descriptor("write")])
    monkeypatch.setattr("agent.modules.tools.resolver.get_registry_service", lambda: service)
    resolver = ToolResolver(include_mcp=False)
    assert len(resolver.resolve_for_agent("restricted", override_tool_names=allowed)) == len(allowed)
    assert resolver.resolve_for_agent("restricted", override_tool_names=["write"]) == []


def test_include_mcp_false_filters_already_loaded_tools(monkeypatch):
    service = ToolRegistryService()
    service.load_descriptors([descriptor(), descriptor("mcp__github__read", ToolSource.MCP)])
    monkeypatch.setattr("agent.modules.tools.resolver.get_registry_service", lambda: service)
    assert [d.name for d in ToolResolver(include_mcp=False).resolve_for_policy(ToolPolicy.allow_all())] == ["read"]


@pytest.mark.parametrize("factory", [WorkflowContext, make_context])
def test_context_preserves_empty_and_unset_whitelists(factory):
    assert factory(allowed_tool_names=[]).get_allowed_tool_names() == []
    assert factory(allowed_tool_names=None).get_allowed_tool_names() is None


@pytest.mark.parametrize("exception", [ToolError(ToolErrorCode.PERMISSION_DENIED, "Denied"),
                                       RuntimeError("Unexpected"), ToolException("Upstream")])
def test_sync_tool_errors_have_error_status(exception):
    @tool
    def failing(value: str) -> str:
        """Fail with the requested exception."""
        raise exception
    result = apply_default_middleware(failing).invoke(
        {"type": "tool_call", "name": "failing", "id": "call", "args": {"value": "x"}})
    assert result.status == "error" and result.content.startswith("[error]")


@pytest.mark.asyncio
async def test_async_artifact_tool_errors_and_validation_have_error_status():
    async def failing(value: int):
        raise ToolError(ToolErrorCode.UPSTREAM, "Unavailable")
    wrapped = apply_default_middleware(StructuredTool.from_function(
        coroutine=failing, name="failing", description="Fail", response_format="content_and_artifact"))
    for value in (1, "invalid"):
        result = await wrapped.ainvoke({"type": "tool_call", "name": "failing", "id": "call",
                                       "args": {"value": value}})
        assert result.status == "error" and result.content.startswith("[error]")


@pytest.mark.asyncio
async def test_error_middleware_preserves_interrupt_and_cancellation():
    for exception in (GraphInterrupt(()), asyncio.CancelledError()):
        async def interrupted():
            raise exception
        wrapped = apply_default_middleware(StructuredTool.from_function(
            coroutine=interrupted, name="interrupt", description="Interrupt"))
        with pytest.raises(type(exception)):
            await wrapped.ainvoke({})


def test_registry_service_preserves_empty_injected_registry():
    registry = ToolRegistry()
    assert ToolRegistryService(registry).registry is registry


def test_source_replacement_is_atomic():
    service = ToolRegistryService()
    previous = descriptor("mcp__github__read", ToolSource.MCP)
    service.load_descriptors([previous])
    with pytest.raises(ValueError):
        service.replace_source(ToolSource.MCP, [descriptor("replacement", ToolSource.MCP), descriptor()])
    assert service.get_descriptors() == [previous]


@pytest.mark.asyncio
async def test_mcp_load_failure_retries_and_preserves_previous_catalog(isolated_container, monkeypatch):
    service = isolated_container.tool_registry_service
    previous = descriptor("mcp__github__read", ToolSource.MCP)
    service.load_descriptors([previous])
    attempts = 0
    async def fetch():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Temporary outage")
        return []
    isolated_container._mcp_service = SimpleNamespace(get_all_tools=fetch)
    assert not await ensure_mcp_loaded(force=True, container=isolated_container)
    assert not isolated_container._mcp_loaded
    assert service.registry.get(previous.id) is previous
    assert await ensure_mcp_loaded(container=isolated_container)
    assert service.registry.get(previous.id) is None and attempts == 2


@pytest.mark.asyncio
async def test_concurrent_mcp_loads_share_one_fetch(isolated_container, monkeypatch):
    attempts = 0
    async def fetch():
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(0.01)
        return []
    isolated_container._mcp_service = SimpleNamespace(get_all_tools=fetch)
    results = await asyncio.gather(*(ensure_mcp_loaded(container=isolated_container) for _ in range(4)))
    assert all(results) and attempts == 1


@pytest.mark.asyncio
async def test_mcp_reload_uses_the_explicit_container(isolated_container, monkeypatch):
    reloaded = []
    isolated_container._mcp_service = SimpleNamespace(reload=lambda: reloaded.append(True))
    async def fetch():
        return []
    isolated_container._mcp_service = SimpleNamespace(get_all_tools=fetch, reload=lambda: reloaded.append(True))
    await reload_mcp_descriptors(container=isolated_container)
    assert reloaded == [True]


@pytest.mark.asyncio
async def test_mcp_loading_does_not_use_another_active_container(isolated_container, tmp_path):
    from agent.bootstrap.container import create_test_container

    @tool("mcp__target__read")
    def target_tool(value: str) -> str:
        """Return a target-scoped value."""
        return value

    target = create_test_container(tmp_path=tmp_path / "target")
    async def fetch():
        return [target_tool]
    target._mcp_service = SimpleNamespace(get_all_tools=fetch)
    assert await ensure_mcp_loaded(container=target)
    assert target.tool_registry_service.get_tool_by_name(target_tool.name) is target_tool
    assert not isolated_container._mcp_loaded
    assert isolated_container.tool_registry_service.get_tool_by_name(target_tool.name) is None


@pytest.mark.asyncio
async def test_sources_share_an_async_protocol(monkeypatch):
    async def fetch():
        return []
    monkeypatch.setattr("agent.modules.mcp.get_all_mcp_tools", fetch)
    for source in (BuiltinToolSource(), McpToolSource()):
        assert isinstance(source, ToolSourceAdapter)
        assert isinstance(await source.aload(), list)


@pytest.mark.asyncio
async def test_mcp_service_retries_failed_server(monkeypatch):
    from agent.modules.mcp import MCPServerConfig, MCPService, MCPTransport
    config = MCPServerConfig(name="server", transport=MCPTransport.STDIO, command="probe")
    service = MCPService(SimpleNamespace(list_servers=lambda: [config]))
    attempts = 0
    async def fetch(server):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Temporary outage")
        return []
    monkeypatch.setattr("agent.modules.mcp.service.fetch_tools_for_config", fetch)
    assert await service.get_all_tools() == []
    assert service.failed_server_names == frozenset({"server"})
    assert await service.get_all_tools() == [] and attempts == 2
    assert not service.failed_server_names


@pytest.mark.asyncio
@pytest.mark.parametrize("has_previous_catalog", [False, True])
async def test_mcp_outage_preserves_only_failed_server_tools_and_retries(
    isolated_container, monkeypatch, has_previous_catalog,
):
    from agent.modules.mcp import MCPServerConfig, MCPService, MCPTransport

    configs = [MCPServerConfig(name=name, transport=MCPTransport.STDIO, command="probe")
               for name in ("healthy", "offline")]
    repository = SimpleNamespace(list_servers=lambda: configs, reload=lambda: None)
    isolated_container._mcp_service = MCPService(repository)
    registry = isolated_container.tool_registry_service
    previous = descriptor("mcp__offline__read", ToolSource.MCP)
    if has_previous_catalog:
        registry.load_descriptors([previous, descriptor("mcp__healthy__old", ToolSource.MCP),
                                   descriptor("mcp__removed__read", ToolSource.MCP)])
        isolated_container._mcp_loaded = True

    @tool("mcp__healthy__read")
    def healthy_read(value: str) -> str:
        """Return a value from the available server."""
        return value

    attempts = {"healthy": 0, "offline": 0}

    async def fetch(config):
        attempts[config.name] += 1
        if config.name == "offline":
            if attempts[config.name] == 1:
                raise RuntimeError("Temporary outage")
            return []
        return [healthy_read]

    monkeypatch.setattr("agent.modules.mcp.service.fetch_tools_for_config", fetch)
    await reload_mcp_descriptors(container=isolated_container)
    assert registry.get_tool_by_name(healthy_read.name) is healthy_read
    assert registry.registry.get(previous.id) is (previous if has_previous_catalog else None)
    assert registry.get_tool_by_name("mcp__healthy__old") is None
    assert registry.get_tool_by_name("mcp__removed__read") is None
    assert not isolated_container._mcp_loaded
    statuses = {status.name: status for status in await isolated_container.mcp_service.list_status()}
    assert statuses["healthy"].loaded and statuses["offline"].error

    assert await ensure_mcp_loaded(container=isolated_container)
    assert registry.get_tool_by_name(healthy_read.name) is healthy_read
    assert registry.registry.get(previous.id) is None
    assert attempts == {"healthy": 1, "offline": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize("name, args, normalized", [
    ("bash", {"command": "echo probe", "timeout_seconds": 60}, {"timeout_seconds": 60.0}),
    ("write", {"path": "probe.txt", "content": "text", "append": "false"}, {"append": False}),
])
async def test_batch_replay_uses_the_same_validated_arguments_as_native_tools(
    tmp_path, monkeypatch, name, args, normalized,
):
    service = CodingService(tmp_path / "runtime")
    context = InvocationContext("default", str(tmp_path), "thread", message_id="message", tool_call_id="settled")
    runtime = SimpleNamespace(context={}, state={}, config={}, tool_call_id=context.tool_call_id)
    coding_tool = make_coding_tool(name)
    executions = []

    async def execute(operation, values, ctx):
        executions.append(values)
        return ToolResult(content="done")

    monkeypatch.setattr(service, "execute", execute)
    monkeypatch.setattr("agent.modules.tools.coding.adapter.get_coding_service", lambda: service)
    monkeypatch.setattr("agent.modules.tools.coding.adapter.invocation_context", lambda runtime: context)
    call = {"type": "tool_call", "name": name, "id": context.tool_call_id, "args": args}
    native_call = {**call, "args": {**args, "runtime": runtime}}
    try:
        assert await prepare_tool_batch([coding_tool], [call], runtime) is None
        assert (await coding_tool.ainvoke(native_call)).status == "success"
        assert await prepare_tool_batch([coding_tool], [call], runtime) is None
        assert (await coding_tool.ainvoke(native_call)).status == "success"
        assert (await service.invoke(coding_tool.coding_definition, args, context)).status == "success"
        assert len(executions) == 1
        for key, value in normalized.items():
            assert executions[0][key] == value and type(executions[0][key]) is type(value)
    finally:
        await service.processes.close()
        await service.storage.close()


@pytest.mark.asyncio
async def test_read_path_works_in_batches_and_native_calls(tmp_path, monkeypatch):
    service = CodingService(tmp_path / "runtime")
    context = InvocationContext("default", str(tmp_path), "thread", tool_call_id="read-call")
    runtime = SimpleNamespace(context={}, state={}, config={}, tool_call_id=context.tool_call_id)
    target = tmp_path / ".k41-agent" / "scratchpad" / "tool-test.md"
    target.parent.mkdir(parents=True)
    target.write_text("tool read probe\n", encoding="utf-8")
    coding_tool = make_coding_tool("read")
    monkeypatch.setattr("agent.modules.tools.coding.adapter.get_coding_service", lambda: service)
    monkeypatch.setattr("agent.modules.tools.coding.adapter.invocation_context", lambda runtime: context)
    args = {"path": ".k41-agent/scratchpad/tool-test.md"}
    call = {"type": "tool_call", "name": "read", "id": context.tool_call_id, "args": args}
    try:
        schema = coding_tool.tool_call_schema.model_json_schema()
        assert "path" in schema["required"] and "file_path" not in schema["properties"]
        assert await prepare_tool_batch([coding_tool], [call], runtime) is None
        message = await coding_tool.ainvoke({**call, "args": {**args, "runtime": runtime}})
        assert message.status == "success"
        assert message.content.startswith("1: tool read probe\n[version=")
        result = await service.invoke(coding_tool.coding_definition, args, context)
        assert result.status == "success" and result.content == message.content
        assert args == {"path": ".k41-agent/scratchpad/tool-test.md"}
    finally:
        await service.processes.close()
        await service.storage.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("name, args", [
    ("read", {}),
    ("write", {"content": "replacement"}),
    ("edit", {"old_string": "original", "new_string": "replacement"}),
    ("list_dir", {}),
    ("glob", {"pattern": "*.txt"}),
    ("grep", {"pattern": "original"}),
])
@pytest.mark.parametrize("invalid_path", [
    {"file_path": "probe.txt"},
    {"path": "probe.txt", "file_path": "probe.txt"},
    {"path": "probe.txt", "unexpected": True},
], ids=["file-path", "both-path-fields", "unknown-field"])
async def test_file_tools_reject_non_schema_fields_before_execution(
    tmp_path, monkeypatch, name, args, invalid_path,
):
    service = CodingService(tmp_path / "runtime")
    context = InvocationContext("default", str(tmp_path), "thread", tool_call_id="invalid-call")
    runtime = SimpleNamespace(context={}, state={}, config={}, tool_call_id=context.tool_call_id)
    coding_tool = make_coding_tool(name)
    monkeypatch.setattr("agent.modules.tools.coding.adapter.get_coding_service", lambda: service)
    monkeypatch.setattr("agent.modules.tools.coding.adapter.invocation_context", lambda runtime: context)

    async def unexpected_execution(*args):
        pytest.fail("Invalid input reached preparation or execution.")

    monkeypatch.setattr(service, "prepare", unexpected_execution)
    monkeypatch.setattr(service, "execute", unexpected_execution)
    call = {"type": "tool_call", "name": name, "id": context.tool_call_id, "args": {**args, **invalid_path}}
    try:
        schema = coding_tool.tool_call_schema.model_json_schema()
        assert "path" in schema["properties"] and "file_path" not in schema["properties"]
        failure = await prepare_tool_batch([coding_tool], [call], runtime)
        assert failure.status == "error" and failure.error.code == "invalid_input"
        assert failure.data["batch_stopped_before_execution"]
        with pytest.raises(ValidationError):
            await coding_tool.ainvoke({**call, "args": {**call["args"], "runtime": runtime}})
        result = await service.invoke(coding_tool.coding_definition, call["args"], context)
        assert result.status == "error" and result.error.code == "invalid_input"
    finally:
        await service.processes.close()
        await service.storage.close()


@pytest.mark.asyncio
async def test_batch_denial_precedes_every_mutation(tmp_path):
    service = CodingService(tmp_path / "runtime")
    context = InvocationContext("restricted", str(tmp_path), "thread", permission_rules=(
        PermissionRule(action="edit", resource="*blocked.txt", effect="deny"),))
    async def execute(params, ctx, runtime):
        return await runtime.execute("write", params.model_dump(), ctx)
    definition = ToolDefinition("write", "Write", SCHEMAS["write"], execute)
    invocations = [CodingInvocation(definition, {"path": path, "content": "text"},
                                    replace(context, tool_call_id=path)) for path in ("allowed.txt", "blocked.txt")]
    try:
        failure = await service.prepare_batch(invocations)
        assert failure.status == "error" and failure.data["batch_stopped_before_execution"]
        assert not (tmp_path / "allowed.txt").exists() and not (tmp_path / "blocked.txt").exists()
    finally:
        await service.processes.close()


@pytest.mark.asyncio
async def test_batch_rejects_reused_call_ids_before_any_new_effect(tmp_path):
    service = CodingService(tmp_path / "runtime")
    context = InvocationContext("restricted", str(tmp_path), "thread", tool_call_id="settled")
    async def execute(params, ctx, runtime):
        return await runtime.execute("write", params.model_dump(), ctx)
    definition = ToolDefinition("write", "Write", SCHEMAS["write"], execute)
    args = {"path": "settled.txt", "content": "original"}
    try:
        await service.invoke(definition, args, context)
        invocations = [
            CodingInvocation(definition, {"path": "fresh.txt", "content": "new"},
                             replace(context, tool_call_id="fresh")),
            CodingInvocation(definition, {**args, "content": "changed"}, context),
        ]
        failure = await service.prepare_batch(invocations)
        assert failure.status == "error" and failure.error.code == "invalid_input"
        assert not (tmp_path / "fresh.txt").exists()
        assert (tmp_path / "settled.txt").read_text(encoding="utf-8") == "original"
    finally:
        await service.processes.close()


def test_task_persistence_preserves_whitelist_semantics():
    from agent.modules.agent_runtime.repository import _dump_json_list, _parse_json_list
    for names in (None, [], ["read"]):
        assert _parse_json_list(_dump_json_list(names)) == names
