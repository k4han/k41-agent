"""K41 operation approvals during an ACP agent's MCP tool call."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from agent.modules.acp.turns import PendingToolPermission
from agent.modules.instabox import InstaboxService, InstaboxSettings
from agent.shared.infrastructure.db.engine import close_async_engine, initialize_async_engine
from tests.test_acp_graph import (
    THREAD, TURN_LOST_NOTICE, env, question_of, resume, texts, types, use_card_tools, visible,
)
from tests.test_instabox import API_KEY, FakeInstabox, MemoryRepository


@pytest_asyncio.fixture
async def instabox(env):
    from agent.shared.infrastructure.db import Base, load_orm_models

    load_orm_models()
    await initialize_async_engine(metadata=Base.metadata, container=env.container)
    fake = FakeInstabox()
    service = InstaboxService(
        env.container, repository=MemoryRepository(), http_client=fake.client(),
        settings_loader=lambda: InstaboxSettings(enabled=True, api_key=API_KEY),
    )
    env.container._instabox_service = service
    try:
        yield SimpleNamespace(fake=fake, service=service)
    finally:
        await service.close()
        await close_async_engine(container=env.container)


def tool_prompt(tool_name="instabox_machine_create", **arguments):
    return "scenario:mcp_tool input:" + json.dumps({"name": tool_name, "arguments": arguments})


def permission_resume(events, decision="allow_once", *, request_id=None):
    question = question_of(events)
    assert question["id"].startswith("permission:")
    return {
        "action": "permission", "request_id": request_id or question["id"].split(":", 1)[1],
        "decision": decision,
    }


def create_requests(instabox):
    return [request for request in instabox.fake.requests
            if request.method == "POST" and request.url.path == "/v1/machines"]


@pytest.mark.asyncio
@pytest.mark.parametrize("permission_mode", ["ask", "auto_allow"])
async def test_machine_creation_waits_for_k41_approval_and_runs_once(env, instabox, permission_mode):
    from agent.modules.tools import pending_permission_requests
    from agent.modules.workspaces import list_thread_resources

    use_card_tools(env, "instabox_machine_create", permission_mode=permission_mode)
    first = await env.collect(tool_prompt(name="dev-sandbox", purpose="Development"))
    if permission_mode == "ask":
        assert question_of(first)["id"].startswith("acp_permission:")
        first = await env.collect(resume_payload=resume(first, "allow"))
    assert "instabox_machine_create: dev-sandbox" in question_of(first)["question"]
    assert not create_requests(instabox) and not await list_thread_resources(THREAD)
    restored = await pending_permission_requests(THREAD)
    assert restored[0]["questions"][0]["id"] == question_of(first)["id"]

    second = await env.collect(resume_payload=permission_resume(first))
    result = json.loads(texts(second))
    assert result["auxiliary"]["target"] in instabox.fake.machines
    assert len(create_requests(instabox)) == 1
    assert len(await list_thread_resources(THREAD)) == 1
    assert len(env.calls("session/prompt")) == 1
    assert ("tool", texts(second)) in visible(await env.messages())
    assert not (await env.state()).next


@pytest.mark.asyncio
async def test_denying_k41_approval_never_creates_a_machine(env, instabox):
    use_card_tools(env, "instabox_machine_create", permission_mode="auto_allow")
    first = await env.collect(tool_prompt())
    second = await env.collect(resume_payload=permission_resume(first, "deny"))
    assert "User denied instabox_machine_create" in texts(second)
    assert not create_requests(instabox) and not instabox.fake.machines
    assert not (await env.state()).next


@pytest.mark.asyncio
@pytest.mark.parametrize("forged", [
    {"action": "permission", "request_id": "another", "decision": "allow_thread"},
    {"action": "acp_permission", "request_id": "another", "option_id": "allow"},
])
async def test_unrelated_answers_cannot_approve_k41_operations(env, instabox, forged):
    use_card_tools(env, "instabox_machine_create", permission_mode="auto_allow")
    first = await env.collect(tool_prompt())
    second = await env.collect(resume_payload=forged)
    assert question_of(second)["id"] == question_of(first)["id"]
    assert not create_requests(instabox)
    third = await env.collect(resume_payload=permission_resume(second))
    assert json.loads(texts(third))["auxiliary"]["target"] in instabox.fake.machines
    assert len(create_requests(instabox)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("effect", ["allow", "deny"])
async def test_explicit_k41_rules_still_apply(env, instabox, effect):
    use_card_tools(env, "instabox_machine_create", permission_mode="auto_allow")
    env.agent.tool_permissions = [{"action": "instabox_machine_create", "effect": effect}]
    events = await env.collect(tool_prompt())
    assert "user_input_request" not in types(events)
    if effect == "deny":
        assert "Policy denies instabox_machine_create" in texts(events)
        assert not create_requests(instabox)
    else:
        assert json.loads(texts(events))["auxiliary"]["target"] in instabox.fake.machines
        assert len(create_requests(instabox)) == 1


@pytest.mark.asyncio
async def test_explicit_ask_rule_can_pause_a_read_only_tool(env, instabox):
    use_card_tools(env, "instabox_machine_list", permission_mode="auto_allow")
    env.agent.tool_permissions = [{"action": "instabox_machine_list", "effect": "ask"}]
    first = await env.collect(tool_prompt("instabox_machine_list"))
    assert "instabox_machine_list" in question_of(first)["question"]
    assert not instabox.fake.requests
    second = await env.collect(resume_payload=permission_resume(first))
    assert json.loads(texts(second)) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["allow_once", "allow_thread"])
async def test_permission_grants_keep_their_original_scope(env, instabox, decision):
    use_card_tools(env, "instabox_machine_create", permission_mode="auto_allow")
    first = await env.collect(tool_prompt())
    await env.collect(resume_payload=permission_resume(first, decision))
    second = await env.collect(tool_prompt())
    if decision == "allow_once":
        assert question_of(second)["id"] != question_of(first)["id"]
        assert len(create_requests(instabox)) == 1
        await env.collect(resume_payload=permission_resume(second))
    else:
        assert "user_input_request" not in types(second)
    assert len(create_requests(instabox)) == 2


@pytest.mark.asyncio
async def test_channels_without_approval_still_refuse_default_ask_operations(env, instabox):
    use_card_tools(env, "instabox_machine_create", permission_mode="auto_allow")
    events = await env.collect(tool_prompt(), platform="telegram", thread="telegram_user_1")
    assert "does not support tool permission approval" in texts(events)
    assert "user_input_request" not in types(events) and not create_requests(instabox)


@pytest.mark.asyncio
async def test_synchronous_api_refuses_instead_of_waiting_for_approval(env, instabox):
    from agent.modules.agent_runtime import runner

    use_card_tools(env, "instabox_machine_create", permission_mode="auto_allow")
    result = await asyncio.wait_for(runner.run_agent_full(
        tool_prompt(), THREAD, "ag", working_dir=str(env.workspace), usage_context={"platform": "api"},
    ), 30)
    assert "does not support tool permission approval" in result
    assert not create_requests(instabox)


@pytest.mark.asyncio
async def test_stopping_an_operation_approval_cancels_and_commits_the_turn(env, instabox):
    use_card_tools(env, "instabox_machine_create", permission_mode="auto_allow")
    await env.collect(tool_prompt())
    turn = env.service.turn((await env.state()).values["acp_turn_id"])
    pending = turn.next_pending()
    assert isinstance(pending, PendingToolPermission)
    assert await env.service.stop_thread(THREAD)
    assert pending.decision.done()
    state = await env.state()
    assert not state.next and turn.committed
    assert ("tool", "The tool call was cancelled.") in visible(state.values["messages"])
    assert not create_requests(instabox)
    assert texts(await env.collect("After the stop")) == "After the stop"


@pytest.mark.asyncio
async def test_answer_after_restart_reports_the_lost_turn_without_replaying_the_operation(env, instabox):
    use_card_tools(env, "instabox_machine_create", permission_mode="auto_allow")
    first = await env.collect(tool_prompt())
    await env.restart_server()
    events = await env.collect(resume_payload=permission_resume(first))
    assert TURN_LOST_NOTICE in texts(events)
    assert len(env.calls("session/prompt")) == 1 and not create_requests(instabox)


@pytest.mark.asyncio
async def test_snapshot_restore_still_requires_k41_confirmation_with_an_allow_rule(env, instabox, monkeypatch):
    from agent.modules.tools import get_coding_service
    from agent.modules.workspaces import (
        SNAPSHOT, activate_thread_machine, record_thread_resource, reserve_thread_machine,
    )

    machine_id = instabox.fake.add_machine()
    snapshot_id = "snap_1"
    instabox.fake.snapshots[snapshot_id] = {"id": snapshot_id, "state": "ready"}
    await activate_thread_machine(await reserve_thread_machine(THREAD, "instabox", limit=2), machine_id)
    await record_thread_resource(THREAD, SNAPSHOT, "instabox", snapshot_id)
    use_card_tools(env, "instabox_snapshot_restore", permission_mode="auto_allow")
    env.agent.tool_permissions = [{"action": "instabox_snapshot_restore", "effect": "allow"}]
    monkeypatch.setattr(get_coding_service().remote, "workspace_processes", AsyncMock(return_value=0))

    first = await env.collect(tool_prompt("instabox_snapshot_restore", machine_id=machine_id, snapshot_id=snapshot_id))
    assert "instabox_snapshot_restore" in question_of(first)["question"]
    assert not any(request.url.path.endswith("/restore") for request in instabox.fake.requests)
    second = await env.collect(resume_payload=permission_resume(first))
    assert not texts(second).startswith("ERROR")
    assert sum(request.url.path.endswith("/restore") for request in instabox.fake.requests) == 1
