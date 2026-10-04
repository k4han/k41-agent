"""Approval transport, replay correlation, and migration contracts."""

from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from agent.modules.tools.coding.models import InvocationContext, PermissionRule
from agent.modules.tools.coding.permissions import Permissions, pending_permission_requests, permission_request_event
from agent.modules.tools.coding.storage import OutputStore


@pytest.mark.asyncio
async def test_wrong_resume_cannot_authorize_an_operation(tmp_path):
    permissions = Permissions(OutputStore(tmp_path / "artifacts"))
    context = InvocationContext("default", str(tmp_path), "thread", tool_call_id="call", approval_supported=True,
                                permission_rules=(PermissionRule(action="edit", effect="ask"),))
    writes = []
    def operation(state):
        permissions.assert_allowed(context, "edit", str(tmp_path / "target.txt"))
        writes.append("effect")
        return {"done": True}
    graph = StateGraph(dict)
    graph.add_node("operation", operation)
    graph.add_edge(START, "operation")
    graph.add_edge("operation", END)
    compiled = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread"}}
    first = await compiled.ainvoke({}, config)
    request_id = first["__interrupt__"][0].value["request_id"]
    second = await compiled.ainvoke(Command(resume={"action": "permission", "request_id": "unrelated", "decision": "allow_thread"}), config)
    assert not writes and "__interrupt__" in second
    result = await compiled.ainvoke(Command(resume={"action": "permission", "request_id": request_id, "decision": "allow_once"}), config)
    assert result["done"] and writes == ["effect"]


def test_permission_projection_has_fixed_choices_and_no_free_text():
    request = {"request_id": "request", "tool_call_id": "call", "action": "shell", "resource": "echo test",
               "metadata": {"shell": "pwsh", "workdir": "workspace"}}
    event = permission_request_event(request, "interrupt")
    assert event["tool_call_id"] == "call" and event["interrupt_id"] == "interrupt"
    question = event["questions"][0]
    assert question["id"] == "permission:request"
    assert question["free_text"]["enabled"] is False
    assert [option["id"] for option in question["options"]] == ["allow_once", "allow_thread", "deny"]


@pytest.mark.asyncio
async def test_pending_permission_requests_are_restored_from_checkpoint(monkeypatch):
    request = {"type": "permission_request", "request_id": "pending", "action": "edit", "resource": "file.txt"}
    seen = []
    async def state(config):
        seen.append(config)
        return SimpleNamespace(tasks=[SimpleNamespace(interrupts=[SimpleNamespace(id="interrupt", value=request)])])
    monkeypatch.setattr("agent.modules.workflows.get_workflow_graph", lambda name: SimpleNamespace(aget_state=state))
    monkeypatch.setattr("agent.modules.workflows.make_run_config", lambda **kwargs: {"configurable": kwargs})
    result = await pending_permission_requests("thread", "checkpoint")
    assert result[0]["questions"][0]["id"] == "permission:pending"
    assert seen[0]["configurable"] == {"thread_id": "thread", "checkpoint_id": "checkpoint"}


def test_permission_resume_transport_preserves_correlation():
    from agent.modules.agent_runtime.runner import _normalize_human_resume_payload, _user_input_request_events_from_value_event
    payload = _normalize_human_resume_payload({"action": "permission", "request_id": "request", "decision": "deny"})
    assert payload.model_dump() == {"action": "permission", "request_id": "request", "decision": "deny"}
    event = _user_input_request_events_from_value_event({"__interrupt__": [SimpleNamespace(value={
        "type": "permission_request", "request_id": "request", "action": "read", "resource": "file", "tool_call_id": "call"}, id="interrupt")]})
    assert event[0]["questions"][0]["id"] == "permission:request"


def test_agent_permissions_roundtrip_ignores_retired_profile():
    from agent.modules.agents.models import AgentCard, AgentConfig
    from agent.modules.agents.parser import parse_agent_markdown_content, serialize_agent_config
    config = AgentConfig(name="coder", graph_type="react_agent", provider="default", tool_profile="legacy",
                         tool_permissions=[{"action": "shell", "resource": "*", "effect": "ask"}])
    restored = parse_agent_markdown_content(serialize_agent_config(config))
    assert restored.tool_permissions == config.tool_permissions
    assert "tool_profile" not in serialize_agent_config(config)
    assert "tool_profile" not in restored.model_dump()
    assert AgentCard.from_config(config, source="user", path="coder.md", editable=True).to_agent_config().tool_permissions == config.tool_permissions


def test_global_coding_keys_are_runtime_database_settings():
    from agent.shared.config.constants import KNOWN_RUNTIME_KEYS, is_database_runtime_key, is_runtime_key
    for key in ("tools.permissions", "tools.shell", "tools.storage_root"):
        assert key in KNOWN_RUNTIME_KEYS and is_runtime_key(key) and is_database_runtime_key(key)


def test_incomplete_valid_json_journal_is_an_unknown_outcome(tmp_path):
    from agent.modules.tools.coding.models import CodingError
    storage = OutputStore(tmp_path / "outputs")
    context = InvocationContext("default", str(tmp_path), "thread", tool_call_id="call")
    storage.journal_path(context).write_text("{}", encoding="utf-8")
    with pytest.raises(CodingError) as caught:
        storage.begin(context, "fingerprint")
    assert caught.value.code == "unknown_outcome"


def test_legacy_shell_propagates_permission_interrupt(monkeypatch, tmp_path):
    import asyncio
    import importlib
    from langgraph.errors import GraphInterrupt
    shell = importlib.import_module("agent.modules.tools.builtin.shell.run_bash_tool")
    monkeypatch.setattr(shell, "get_workspace", lambda runtime: SimpleNamespace(backend="local", locator=str(tmp_path)))
    async def interrupted(*args, **kwargs):
        raise GraphInterrupt(())
    monkeypatch.setattr(shell, "_run_local", interrupted)
    with pytest.raises(GraphInterrupt):
        asyncio.run(shell.run_bash.coroutine("echo test", SimpleNamespace()))


def test_global_permission_editor_validates_json_and_rules():
    from agent.modules.tools import normalize_tool_setting_value
    assert normalize_tool_setting_value("tools.permissions", '[{"action":"shell","effect":"ask"}]') == [
        {"action": "shell", "resource": "*", "effect": "ask"}]
    for invalid in ('{"effect":"allow"}', "[", '[{"effect":"allow","resorce":"typo"}]'):
        with pytest.raises(ValueError):
            normalize_tool_setting_value("tools.permissions", invalid)
