"""Conversation overrides survive API, routing and runner boundaries."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agent.delivery.http.api.schemas import ChatRequest, EditChatRequest
from agent.delivery.http.api.router import _request_to_run_params
from agent.modules.agent_runtime.runner import build_run_params, resolve_approval_supported
from agent.modules.agents.models import AgentConfig
from agent.modules.tools.coding.adapter import invocation_context
from agent.modules.tools.permissions import evaluate
from agent.modules.workflows.graphs.router import _build_target_context
from agent.modules.workflows.run_config import make_context


@pytest.mark.parametrize("mode", ["ask", "auto", "full_access"])
@pytest.mark.parametrize("resume", [False, True])
def test_chat_new_and_resume_mode_reaches_context(mode, resume, tmp_path, monkeypatch):
    request = ChatRequest(message="", thread_id="api_test", permission_mode=mode, resume=resume)
    params = _request_to_run_params(request)
    assert build_run_params(platform="api", user_id="test", user_input="", permission_mode=mode)["permission_mode"] == mode
    context = make_context(working_dir=str(tmp_path), permission_mode=params["permission_mode"])
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(
        get_agent=lambda name: SimpleNamespace(tool_permissions=None, permission_mode="auto")))
    policy = invocation_context(SimpleNamespace(context=context, config={"configurable": {"thread_id": "api_test"}}))
    assert policy.permission_mode == mode
    assert evaluate(policy, "edit", "file") == ("ask" if mode == "ask" else "allow")


@pytest.mark.parametrize("schema,extra", [(ChatRequest, {}), (EditChatRequest, {
    "thread_id": "api_test", "message_index": 0, "source_checkpoint_id": "checkpoint"})])
def test_requests_validate_override(schema, extra):
    assert schema(message="hi", permission_mode="ask", **extra).permission_mode == "ask"
    with pytest.raises(ValidationError):
        schema(message="hi", permission_mode="auto_allow", **extra)


def test_edit_endpoint_passes_mode_to_the_edit_runner(isolated_container, monkeypatch):
    import importlib
    from tests.test_api_router import _create_client
    router = importlib.import_module("agent.delivery.http.api.router")
    events = importlib.import_module("agent.delivery.http.api.events_v2")
    captured = {}
    async def not_acp(*args):
        return False
    async def apply_workspace(*args):
        return None
    async def subscribe(**kwargs):
        yield {"type": "final", "content": "done"}
    async def session(thread_id, params, *, run_fn):
        captured.update(params)
        captured["runner"] = run_fn
        return SimpleNamespace(subscribe=subscribe, run_id="run", thread_id=thread_id)
    monkeypatch.setattr(router, "get_catalog_service", lambda: SimpleNamespace(
        get_agent_card=lambda name: SimpleNamespace(graph_type="react_agent")))
    monkeypatch.setattr(router, "_ensure_chat_agent_available", lambda name: None)
    monkeypatch.setattr(router, "_validate_pool_request", lambda request: None)
    monkeypatch.setattr(router, "thread_is_acp", not_acp)
    monkeypatch.setattr(router, "_apply_workspace_to_run_params", apply_workspace)
    monkeypatch.setattr(events, "get_chat_stream_manager", lambda: SimpleNamespace(get_or_create_session=session))
    response = _create_client().post("/v2/api/chat/events/edit", json={
        "message": "edited", "thread_id": "api_test", "message_index": 0,
        "source_checkpoint_id": "checkpoint", "permission_mode": "ask"})
    assert response.status_code == 200
    assert captured["permission_mode"] == "ask"
    assert captured["runner"] is events.run_agent_edit_protocol_stream


@pytest.mark.parametrize("override", [None, "ask"])
def test_router_keeps_override_without_resolving_it_for_the_wrong_card(tmp_path, monkeypatch, override):
    source = make_context(working_dir=str(tmp_path), permission_mode=override)
    target_agent = AgentConfig(name="target", graph_type="react_agent", permission_mode="full_access")
    target = _build_target_context(source, target_agent)
    assert target.permission_mode == override
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(get_agent=lambda name: target_agent))
    context = invocation_context(SimpleNamespace(context=target, config={"configurable": {"thread_id": "api_test"}}))
    assert context.permission_mode == (override or "full_access")


@pytest.mark.parametrize("platform,surfaces,expected", [("api", True, True), ("api", False, False),
    ("telegram", True, False), ("bg", True, False), ("delegate", True, False)])
def test_one_approval_capability(platform, surfaces, expected):
    assert resolve_approval_supported({"platform": platform}, "api_thread", surfaces_interrupts=surfaces) is expected


def test_acp_requires_an_explicit_upgrade_choice():
    with pytest.raises(ValidationError, match="explicit permission_mode"):
        AgentConfig(name="legacy", graph_type="acp_agent")
    with pytest.raises(ValidationError) as error:
        AgentConfig(name="legacy", graph_type="acp_agent", acp={"permission_mode": "auto_allow"})
    assert "recommended" in str(error.value) and "full_access" in str(error.value)
    assert AgentConfig(name="new", graph_type="acp_agent", permission_mode=None).permission_mode is None
