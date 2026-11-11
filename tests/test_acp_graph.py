"""ACP agent graph driven through the real runner against the fake agent."""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError
from sqlalchemy import MetaData

from agent.modules.acp.errors import AcpNotInstalled
from agent.modules.acp.process import AgentCommand
from agent.modules.acp.service import AcpService, not_installed_notice
from agent.modules.acp.translator import TurnTranscript
from agent.modules.acp.turns import SIGN_IN_NOTICE, STOP_TURN_OPTION_ID
from agent.modules.agent_runtime import runner as runner_module
from agent.modules.agents.models import AcpAgentSettings, AgentConfig
from agent.modules.agents.parser import parse_agent_markdown_content, serialize_agent_config
from agent.modules.browser.models import BrowserArtifact, BrowserBinding, BrowserInvocation, BrowserProfile, BrowserProjectProfile
from agent.modules.workspaces.models import ThreadWorkspace
from agent.modules.browser.service import BrowserService
from agent.modules.tools.coding.models import PermissionResume
from agent.modules.workflows import delete_workflow_thread_tree
from agent.modules.workflows.graphs.acp_agent import TURN_LOST_NOTICE, CardToolProvider, compile_acp_agent_graph
from agent.modules.workflows.prompt_builders import BROWSER_REPORTING_RULES
from agent.shared.infrastructure.db.engine import close_async_engine, initialize_async_engine

FAKE_AGENT = Path(__file__).parent / "fixtures" / "acp" / "fake_agent.py"
THREAD = "api_dashboard_acp-thread"


class Env:
    def __init__(self, tmp_path, container, monkeypatch, graph):
        self.state_dir = tmp_path / "fake-state"
        self.workspace = tmp_path / "workspace"
        self.workspace.mkdir()
        self.container = container
        self.monkeypatch = monkeypatch
        self.graph = graph
        self.flags: list[str] = []
        self.agent = AgentConfig(permission_mode="ask", name="ag", graph_type="acp_agent", acp=AcpAgentSettings())
        self.service = self.new_service()

    def new_service(self) -> AcpService:
        service = AcpService(command_resolver=lambda _agent: AgentCommand(
            argv=[sys.executable, "-I", "-u", str(FAKE_AGENT), str(self.state_dir), *self.flags],
            cwd=self.state_dir,
        ))
        self.container._acp_service = service
        return service

    async def restart_server(self) -> None:
        await self.service.close()
        self.service = self.new_service()

    def stream(self, text="", *, thread=THREAD, platform="api", working_dir=None, **kwargs):
        return runner_module.run_agent_stream(
            user_input=text, thread_id=thread, agent_name="ag", working_dir=str(working_dir or self.workspace),
            usage_context={"platform": platform}, **kwargs,
        )

    async def collect(self, text="", **kwargs) -> list[dict]:
        async def consume():
            return [event async for event in self.stream(text, **kwargs)]

        return await asyncio.wait_for(consume(), 30)

    async def state(self, thread=THREAD):
        return await self.graph.aget_state({"configurable": {"thread_id": thread}})

    async def messages(self, thread=THREAD):
        return (await self.state(thread)).values["messages"]

    def calls(self, method: str) -> list[dict]:
        path = self.state_dir / "calls.jsonl"
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return [row for row in rows if row.get("method") == method]

    def effects(self) -> list[str]:
        path = self.state_dir / "effects.log"
        return path.read_text(encoding="utf-8").split() if path.exists() else []


@pytest_asyncio.fixture
async def env(tmp_path, isolated_container, monkeypatch):
    graph = compile_acp_agent_graph(InMemorySaver())
    environment = Env(tmp_path, isolated_container, monkeypatch, graph)
    monkeypatch.setattr(runner_module, "get_workflow_graph", lambda name: graph)
    monkeypatch.setattr("agent.modules.workflows.get_workflow_graph", lambda name: graph)
    monkeypatch.setattr("agent.modules.agents.get_catalog_service", lambda: SimpleNamespace(
        get_agent=lambda name: environment.agent, get_card=lambda name: None,
    ))

    async def no_prompt_variables(*_args, **_kwargs):
        return {}

    monkeypatch.setattr("agent.modules.prompt_variables.get_runtime_prompt_variable_values", no_prompt_variables)

    async def no_record(**_kwargs):
        return None

    monkeypatch.setattr(runner_module, "_record_conversation_thread", no_record)
    yield environment
    await environment.service.close()


def types(events):
    return [event["type"] for event in events]


def texts(events):
    return "".join(event["content"] for event in events if event["type"] == "message")


def request_of(events):
    return next(event for event in events if event["type"] == "user_input_request")


def question_of(events):
    return request_of(events)["questions"][0]


def resume(events, option_id, *, request_id=None):
    identifier = request_id or question_of(events)["id"].split(":", 1)[1]
    return {"action": "acp_permission", "request_id": identifier, "option_id": option_id}


def resume_k41(events, decision="allow_once"):
    identifier = question_of(events)["id"]
    assert identifier.startswith("permission:")
    return {"action": "permission", "request_id": identifier.split(":", 1)[1], "decision": decision}


def visible(messages):
    """History as the dashboard reloads it: (role, text-or-name)."""
    rows = []
    for message in messages:
        if isinstance(message, HumanMessage):
            rows.append(("user", message.content))
        elif isinstance(message, ToolMessage):
            rows.append(("tool", message.content))
        elif isinstance(message, AIMessage) and message.tool_calls:
            rows.append(("call", message.tool_calls[0]["name"]))
        elif isinstance(message, AIMessage):
            rows.append(("ai", message.content))
    return rows


# Text and tools ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_text_turn_streams_once_and_persists(env):
    events = await env.collect("scenario:text")
    assert texts(events) == "Hello world"
    finals = [event for event in events if event["type"] == "final"]
    assert finals == [{"type": "final", "content": "Hello world", "already_streamed": True}]
    assert next(event for event in events if event["type"] == "context_usage")["current_context_tokens"] == 123
    assert visible(await env.messages()) == [("user", "scenario:text"), ("ai", "Hello world")]
    assert not (await env.state()).next


@pytest.mark.asyncio
@pytest.mark.parametrize("emit_thinking", [True, False])
async def test_thinking_survives_checkpoint_history_without_duplicate_events(env, emit_thinking):
    from agent.modules.conversations.history import _serialize_thread_messages

    events = await env.collect("scenario:thinking", emit_thinking=emit_thinking)
    thoughts = [event["content"] for event in events if event["type"] == "thinking"]
    assert thoughts == (["Check the ", "cache."] if emit_thinking else [])
    assert texts(events) == "The cache is warm."
    assert events[-1] == {"type": "final", "content": "The cache is warm.", "already_streamed": True}
    entries = _serialize_thread_messages(await env.messages())
    assert [(entry["role"], entry["content"], entry.get("thinking")) for entry in entries] == [
        ("user", "scenario:thinking", None),
        ("assistant", "", "Check the cache."),
        ("assistant", "The cache is warm.", None),
    ]


@pytest.mark.asyncio
async def test_tool_events_appear_once_and_persist_in_order(env):
    events = await env.collect("scenario:tools")
    assert [event["type"] for event in events if event["type"] != "context_usage"] == [
        "message", "tool_call", "tool_result", "message", "final",
    ]
    call = next(event for event in events if event["type"] == "tool_call")
    result = next(event for event in events if event["type"] == "tool_result")
    assert call["name"] == "acp_execute" and call["args"]["title"] == "echo hi"
    assert result["tool_call_id"] == call["id"] and result["content"] == "hi\r\n"
    assert visible(await env.messages()) == [
        ("user", "scenario:tools"), ("ai", "Running."), ("call", "acp_execute"), ("tool", "hi\r\n"), ("ai", "Done."),
    ]


@pytest.mark.asyncio
async def test_turn_ending_on_a_tool_still_gives_a_final_message(env):
    events = await env.collect("scenario:ends_on_tool")
    assert events[-1]["type"] == "final" and events[-1]["content"]
    assert isinstance((await env.messages())[-1], AIMessage)


@pytest.mark.asyncio
async def test_non_spec_stop_reason_finishes_normally(env):
    events = await env.collect("scenario:bad_stop")
    assert events[-1] == {"type": "final", "content": "Quota message.", "already_streamed": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(("scenario", "expected"), [
    ("text", "Hello world"),
    ("thinking", "The cache is warm."),
    ("thinking_after_text", "The cache is warm."),
])
async def test_non_streaming_path_returns_the_final_text(env, scenario, expected):
    result = await asyncio.wait_for(runner_module.run_agent_full(
        f"scenario:{scenario}", THREAD, "ag", working_dir=str(env.workspace), usage_context={"platform": "api"},
    ), 30)
    assert result == expected


# Permissions ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_transcript_survives_a_pending_permission(env):
    events = await env.collect("scenario:permission")
    assert types(events)[-1] == "user_input_request"
    assert (await env.state()).next == ("acp_permission",)
    assert visible(await env.messages()) == [("user", "scenario:permission"), ("ai", "Before. ")]
    assert env.effects() == []


@pytest.mark.asyncio
async def test_card_offers_agent_options_and_stop(env):
    events = await env.collect("scenario:permission")
    question = question_of(events)
    assert [option["id"] for option in question["options"]] == ["allow", "deny", "__allow_thread__", STOP_TURN_OPTION_ID]
    assert "Run create_file?" in question["question"] and "hello.txt" in question["question"]
    assert question["free_text"]["enabled"] is False


@pytest.mark.asyncio
async def test_allow_runs_the_operation_and_hides_the_noise_call(env):
    first = await env.collect("scenario:permission")
    second = await env.collect(resume_payload=resume(first, "allow"))
    assert env.effects() == ["edit-1"]
    assert texts(second) == "After."
    assert [event["name"] for event in second if event["type"] == "tool_call"] == ["acp_edit"]
    assert second[-1]["type"] == "final"
    assert visible(await env.messages()) == [
        ("user", "scenario:permission"), ("ai", "Before. "), ("call", "acp_edit"), ("tool", "Create hello.txt"), ("ai", "After."),
    ]


@pytest.mark.asyncio
async def test_deny_prevents_the_operation_and_shows_a_failed_tool(env):
    first = await env.collect("scenario:permission")
    second = await env.collect(resume_payload=resume(first, "deny"))
    assert env.effects() == []
    assert next(event for event in second if event["type"] == "tool_result")["content"] == "Rejected by user"


@pytest.mark.asyncio
@pytest.mark.parametrize("forged", ["allow_always", "__anything__"])
async def test_option_that_was_not_offered_authorizes_nothing(env, forged):
    first = await env.collect("scenario:permission")
    second = await env.collect(resume_payload=resume(first, forged))
    assert env.effects() == []
    assert types(second)[-1] == "user_input_request"
    third = await env.collect(resume_payload=resume(second, "allow"))
    assert env.effects() == ["edit-1"] and third[-1]["type"] == "final"


@pytest.mark.asyncio
async def test_resume_for_another_request_authorizes_nothing(env):
    first = await env.collect("scenario:permission")
    second = await env.collect(resume_payload=resume(first, "allow", request_id="unrelated"))
    assert env.effects() == [] and types(second)[-1] == "user_input_request"


@pytest.mark.asyncio
async def test_stop_option_ends_the_turn_and_keeps_output(env):
    first = await env.collect("scenario:permission")
    second = await env.collect(resume_payload=resume(first, STOP_TURN_OPTION_ID))
    assert env.effects() == [] and not (await env.state()).next
    assert second[-1] == {"type": "final", "content": "Before. ", "already_streamed": True}
    assert visible(await env.messages()) == [("user", "scenario:permission"), ("ai", "Before. ")]


@pytest.mark.asyncio
async def test_sequential_permissions_each_pause(env):
    first = await env.collect("scenario:two_permissions")
    second = await env.collect(resume_payload=resume(first, "allow"))
    assert types(second)[-1] == "user_input_request" and env.effects() == ["edit-1"]
    third = await env.collect(resume_payload=resume(second, "allow"))
    assert env.effects() == ["edit-1", "edit-2"]
    assert third[-1]["content"] == "Three."
    assert [text for role, text in visible(await env.messages()) if role == "ai"] == ["One. ", "Two. ", "Three."]


@pytest.mark.asyncio
async def test_concurrent_permissions_are_asked_one_at_a_time(env):
    first = await env.collect("scenario:concurrent_permissions")
    second = await env.collect(resume_payload=resume(first, "allow"))
    assert types(second)[-1] == "user_input_request"
    assert question_of(second)["id"] != question_of(first)["id"]
    third = await env.collect(resume_payload=resume(second, "deny"))
    answers = json.loads(third[-1]["content"])
    assert sorted(answers.values()) == ["allow", "deny"]


@pytest.mark.asyncio
async def test_surface_without_approval_denies_automatically(env):
    events = await env.collect("scenario:permission", thread="telegram_user_chat", platform="telegram")
    assert "user_input_request" not in types(events)
    assert env.effects() == []
    assert events[-1]["type"] == "final" and "After." in events[-1]["content"]


@pytest.mark.asyncio
async def test_non_streaming_path_denies_instead_of_hanging(env):
    result = await asyncio.wait_for(runner_module.run_agent_full(
        "scenario:permission", THREAD, "ag", working_dir=str(env.workspace), usage_context={"platform": "api"},
    ), 30)
    assert "approval_unavailable" in result and result.endswith("After.") and env.effects() == []


@pytest.mark.asyncio
async def test_auto_card_allows_routine_native_edits(env):
    env.agent = AgentConfig(permission_mode="auto", name="ag", graph_type="acp_agent", acp=AcpAgentSettings())
    events = await env.collect("scenario:permission")
    assert "user_input_request" not in types(events) and env.effects() == ["edit-1"]


@pytest.mark.asyncio
async def test_native_conversation_grant_is_exact_and_persists_across_turns(env):
    first = await env.collect("scenario:two_permissions")
    second = await env.collect(resume_payload=resume(first, "__allow_thread__"))
    assert "user_input_request" not in types(second)
    assert env.effects() == ["edit-1", "edit-2"]
    third = await env.collect("scenario:permission")
    assert "user_input_request" not in types(third)
    assert env.effects() == ["edit-1", "edit-2", "edit-1"]


@pytest.mark.asyncio
async def test_pending_native_approval_rechecks_deny_on_resume(env):
    first = await env.collect("scenario:permission")
    env.agent.tool_permissions = [{"action": "edit", "effect": "deny"}]
    second = await env.collect(resume_payload=resume(first, "allow"), permission_mode="full_access")
    assert "permission_denied" in texts(second) and env.effects() == []


def test_acp_resume_cannot_pass_as_a_coding_approval():
    with pytest.raises(ValidationError):
        PermissionResume.model_validate({"action": "acp_permission", "request_id": "x", "option_id": "allow"})


@pytest.mark.asyncio
async def test_pending_request_is_restored_after_a_reload(env, monkeypatch):
    from agent.modules.tools import pending_permission_requests

    first = await env.collect("scenario:permission")

    async def thread_record(_thread_id):
        return {"agent_name": "ag"}

    monkeypatch.setattr("agent.modules.conversations.get_conversation_thread", thread_record)
    monkeypatch.setattr("agent.modules.workflows.get_workflow_graph", lambda name: {"acp_agent": env.graph}[name])
    restored = await pending_permission_requests(THREAD)
    assert restored[0]["questions"][0]["id"] == question_of(first)["id"]


# Stopping ---------------------------------------------------------------------


async def start_slow_turn(env):
    queue: asyncio.Queue = asyncio.Queue()

    async def consume():
        async for event in env.stream("scenario:slow"):
            await queue.put(event)

    task = asyncio.create_task(consume())
    while True:
        event = await asyncio.wait_for(queue.get(), 20)
        if event["type"] == "message":
            return task, queue


@pytest.mark.asyncio
async def test_graceful_stop_commits_partial_output_and_ends_the_run(env):
    task, _queue = await start_slow_turn(env)
    assert await env.service.stop_thread(THREAD) is True
    await asyncio.wait_for(task, 10)
    state = await env.state()
    assert not state.next
    last = state.values["messages"][-1]
    assert isinstance(last, AIMessage) and last.content.startswith("w0")
    assert "cancelled by the client" not in last.content


@pytest.mark.asyncio
async def test_hard_cancel_then_continue_never_resends_the_prompt(env):
    task, _queue = await start_slow_turn(env)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await env.state()).next == ("acp_turn",)
    events = await env.collect(resume=True)
    assert len(env.calls("session/prompt")) == 1
    assert events[-1]["type"] == "final"
    assert isinstance((await env.messages())[-1], AIMessage)


@pytest.mark.asyncio
async def test_stop_while_paused_then_answer_commits_partial_output(env):
    first = await env.collect("scenario:permission")
    assert await env.service.stop_thread(THREAD) is True
    second = await env.collect(resume_payload=resume(first, "allow"))
    assert env.effects() == [] and not (await env.state()).next
    assert second[-1]["content"] == "Before. "


@pytest.mark.asyncio
async def test_new_message_during_a_pause_ends_the_old_turn_first(env):
    await env.collect("scenario:permission")
    await env.collect("second message")
    assert env.effects() == []
    assert len(env.calls("session/prompt")) == 2
    assert len(env.calls("session/new")) == 1
    assert visible(await env.messages())[-1] == ("ai", "second message")
    assert not (await env.state()).next


# Restart and sessions ---------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_while_paused_reports_a_lost_turn_without_resending(env):
    first = await env.collect("scenario:permission")
    await env.restart_server()
    events = await env.collect(resume_payload=resume(first, "allow"))
    assert TURN_LOST_NOTICE in texts(events)
    assert len(env.calls("session/prompt")) == 1 and env.effects() == []
    assert not (await env.state()).next


@pytest.mark.asyncio
async def test_restart_mid_turn_then_continue_reports_a_lost_turn(env):
    task, _queue = await start_slow_turn(env)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await env.restart_server()
    events = await env.collect(resume=True)
    assert TURN_LOST_NOTICE in texts(events)
    assert len(env.calls("session/prompt")) == 1


@pytest.mark.asyncio
async def test_second_message_reuses_the_live_session(env):
    await env.collect("one")
    await env.collect("two")
    assert len(env.calls("session/new")) == 1 and not env.calls("session/resume")
    prompts = env.calls("session/prompt")
    assert prompts[0]["params"]["sessionId"] == prompts[1]["params"]["sessionId"]


@pytest.mark.asyncio
async def test_session_resumes_after_a_restart(env):
    await env.collect("one")
    await env.restart_server()
    events = await env.collect("two")
    assert len(env.calls("session/new")) == 1 and len(env.calls("session/resume")) == 1
    assert texts(events) == "two"


@pytest.mark.asyncio
async def test_load_replay_is_not_shown_again(env):
    await env.collect("one")
    await env.restart_server()
    env.flags = ["--no-resume"]
    events = await env.collect("two")
    assert len(env.calls("session/load")) == 1 and texts(events) == "two"
    assert "old reply" not in str(await env.messages())


@pytest.mark.asyncio
async def test_unrestorable_session_starts_fresh_with_a_notice(env):
    await env.collect("one")
    await env.restart_server()
    for marker in env.state_dir.glob("session-*"):
        marker.unlink()
    events = await env.collect("two")
    assert "could not be restored" in texts(events) and texts(events).endswith("two")
    assert len(env.calls("session/new")) == 2


@pytest.mark.asyncio
async def test_changed_workspace_starts_a_new_session_with_a_notice(env, tmp_path):
    await env.collect("one")
    other = tmp_path / "other"
    other.mkdir()
    events = await env.collect("two", working_dir=other)
    assert "workspace changed" in texts(events)
    sessions = env.calls("session/new")
    assert len(sessions) == 2 and sessions[1]["params"]["cwd"] == str(other.resolve())


@pytest.mark.asyncio
async def test_missing_thread_workspace_is_created_before_the_session(env, tmp_path):
    missing = tmp_path / "not-yet" / "created"
    await env.collect("hello", working_dir=missing)
    assert missing.is_dir()
    assert env.calls("session/new")[0]["params"]["cwd"] == str(missing.resolve())


# Failures ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crash_keeps_partial_output_and_next_message_recovers(env):
    events = await env.collect("scenario:crash")
    assert "stopped unexpectedly" in texts(events)
    assert visible(await env.messages())[1] == ("ai", "Partial. ")
    following = await env.collect("after the crash")
    assert texts(following) == "after the crash"


@pytest.mark.asyncio
async def test_missing_binary_is_reported_in_the_conversation(env):
    def not_installed(agent_id):
        raise AcpNotInstalled(not_installed_notice(agent_id))

    env.service._resolve_command = not_installed
    events = await env.collect("hello")
    assert "not installed" in texts(events) and events[-1]["type"] == "final"
    assert "not installed" in (await env.messages())[-1].content


@pytest.mark.asyncio
async def test_unauthenticated_agent_is_reported_in_the_conversation(env):
    env.flags = ["--require-auth"]
    events = await env.collect("hello")
    assert texts(events) == SIGN_IN_NOTICE


@pytest.mark.asyncio
async def test_remote_workspace_is_refused_before_starting_an_agent(env):
    events = await env.collect("hello", workspace={"backend": "daytona", "locator": "sandbox-1", "metadata": {"root": "/w"}})
    assert "local workspace" in texts(events)
    assert not env.calls("initialize")


# Settings ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_prompt_is_sent_through_the_vendor_append_field(env):
    env.agent = AgentConfig(permission_mode="ask", name="ag", graph_type="acp_agent", system_prompt="Be brief.", acp=AcpAgentSettings())
    await env.collect("hello")
    params = env.calls("session/new")[0]["params"]
    assert params["_meta"]["antigravity"]["systemPrompt"]["append"] == "Be brief."
    assert "Be brief." not in json.dumps(env.calls("session/prompt"))


@pytest.mark.asyncio
@pytest.mark.parametrize("tools, template, expected", [
    (["browser_snapshot"], "Be brief.", f"Be brief.\n\n{BROWSER_REPORTING_RULES}"),
    (["browser_run"], "", BROWSER_REPORTING_RULES),
    (["read"], "Be brief.", "Be brief."),
])
async def test_browser_reporting_rules_follow_the_agents_tools(env, tools, template, expected):
    env.flags = ["--mcp-http"]
    env.agent = AgentConfig(permission_mode="ask", name="ag", graph_type="acp_agent", tools=tools, system_prompt=template, acp=AcpAgentSettings())
    await env.collect("hello", browser_owner_id=OWNER)
    assert env.calls("session/new")[0]["params"]["_meta"]["antigravity"]["systemPrompt"]["append"] == expected


@pytest.mark.asyncio
async def test_a_tool_lookup_failure_does_not_stop_the_session_from_starting(env, monkeypatch):
    async def fail(self):
        raise ValueError("catalog unavailable")

    monkeypatch.setattr(CardToolProvider, "tool_names", fail)
    env.flags = ["--mcp-http"]
    env.agent = AgentConfig(permission_mode="ask", name="ag", graph_type="acp_agent", tools=["browser_run"], system_prompt="Be brief.", acp=AcpAgentSettings())
    await env.collect("hello", browser_owner_id=OWNER)
    assert env.calls("session/new")[0]["params"]["_meta"]["antigravity"]["systemPrompt"]["append"] == "Be brief."


@pytest.mark.asyncio
async def test_config_options_are_applied_once_per_session(env):
    env.agent = AgentConfig(permission_mode="ask", name="ag", graph_type="acp_agent", acp=AcpAgentSettings(
        config_options={"model": "fake-lite", "thinking_level": "high"},
    ))
    await env.collect("one")
    await env.collect("two")
    applied = env.calls("session/set_config_option")
    assert [(call["params"]["configId"], call["params"]["value"]) for call in applied] == [("model", "fake-lite")]


@pytest.mark.asyncio
async def test_unavailable_config_option_is_reported_and_the_turn_continues(env):
    env.agent = AgentConfig(permission_mode="ask", name="ag", graph_type="acp_agent", acp=AcpAgentSettings(config_options={"model": "retired"}))
    events = await env.collect("hello")
    assert "kept its current 'model'" in texts(events) and texts(events).endswith("hello")


@pytest.mark.asyncio
async def test_run_options_override_the_card_for_that_turn(env):
    env.agent = AgentConfig(permission_mode="ask", name="ag", graph_type="acp_agent", acp=AcpAgentSettings(
        config_options={"model": "fake-lite", "thinking_level": "low"},
    ))
    await env.collect("one", acp_config_options={"thinking_level": "high"})
    applied = env.calls("session/set_config_option")
    assert [(call["params"]["configId"], call["params"]["value"]) for call in applied] == [("model", "fake-lite")]


@pytest.mark.asyncio
async def test_run_options_can_change_during_a_conversation(env):
    await env.collect("one", acp_config_options={"thinking_level": "low"})
    await env.collect("two", acp_config_options={"thinking_level": "low"})
    await env.collect("three", acp_config_options={"thinking_level": "high"})
    applied = env.calls("session/set_config_option")
    assert [call["params"]["value"] for call in applied] == ["low", "high"]
    assert len({call["params"]["sessionId"] for call in env.calls("session/prompt")}) == 1


@pytest.mark.asyncio
async def test_run_options_are_applied_again_after_the_agent_process_restarts(env):
    # The real agent resumes a session with its default settings, as the fake does.
    await env.collect("one", acp_config_options={"thinking_level": "low"})
    await env.service.restart_client("antigravity-acp")
    await env.collect("two", acp_config_options={"thinking_level": "low"})
    assert len(env.calls("session/resume")) == 1
    assert [call["params"]["value"] for call in env.calls("session/set_config_option")] == ["low", "low"]


@pytest.mark.asyncio
async def test_model_is_set_before_the_options_that_depend_on_it(env):
    await env.collect("one", acp_config_options={"thinking_level": "low", "model": "fake-lite"})
    applied = env.calls("session/set_config_option")
    assert [call["params"]["configId"] for call in applied] == ["model", "thinking_level"]


@pytest.mark.asyncio
async def test_mode_cannot_be_changed_because_it_would_bypass_approvals(env):
    env.agent = AgentConfig(permission_mode="ask", name="ag", graph_type="acp_agent", acp=AcpAgentSettings(config_options={"mode": "yolo"}))
    first = await env.collect("one")
    second = await env.collect("two", acp_config_options={"mode": "yolo"})
    assert env.calls("session/set_config_option") == []
    assert "K41 controls tool approvals" in texts(first) and "K41 controls tool approvals" in texts(second)


def test_build_run_params_passes_acp_options_only_when_set():
    with_options = runner_module.build_run_params(
        platform="api", user_id="u", user_input="hi", thread_id=THREAD, acp_config_options={"model": "m"},
    )
    without = runner_module.build_run_params(platform="api", user_id="u", user_input="hi", thread_id=THREAD)
    assert with_options["acp_config_options"] == {"model": "m"} and "acp_config_options" not in without


def test_card_round_trips_acp_settings_and_keeps_only_usable_tool_settings():
    config = AgentConfig(permission_mode="auto",
        name="ag", graph_type="acp_agent", tools=["read", "ask_user", "skill", "delegate_task"],
        tool_configs={"read": {"x": 1}}, sub_agents=["x"], plan_approval_targets=["y"],
        acp=AcpAgentSettings(config_options={"model": "m"}),
    )
    assert config.tools == ["read"] and config.sub_agents is None and config.plan_approval_targets == []
    assert config.tool_configs == {"read": {"x": 1}}
    parsed = parse_agent_markdown_content(serialize_agent_config(config), source_label="test")
    assert parsed.acp == config.acp and parsed.graph_type == "acp_agent" and parsed.tools == ["read"]


# K41 tools --------------------------------------------------------------------


def use_card_tools(env, *tools, permission_mode="ask"):
    env.flags = ["--mcp-http"]
    env.agent = AgentConfig(permission_mode=permission_mode, name="ag", graph_type="acp_agent", tools=list(tools),
                            acp=AcpAgentSettings())


def attached_servers(env, method="session/new", index=-1):
    return env.calls(method)[index]["params"]["mcpServers"]


def mcp_choices(env):
    path = env.state_dir / "mcp_choices.log"
    return path.read_text(encoding="utf-8").split() if path.exists() else []


@pytest.mark.asyncio
async def test_a_card_without_tools_attaches_no_server(env):
    env.flags = ["--mcp-http"]
    await env.collect("scenario:text")
    assert attached_servers(env) == []


@pytest.mark.asyncio
async def test_card_tools_are_listed_with_their_schemas(env):
    use_card_tools(env, "get_current_time")
    events = await env.collect("scenario:mcp_list")
    [server] = attached_servers(env)
    assert server["type"] == "http" and server["name"] == "k41" and server["url"].startswith("http://127.0.0.1:")
    assert server["headers"][0]["value"].startswith("Bearer ")
    # "read" is limited to retained output, as for other agents, so long results stay reachable.
    assert [tool["name"] for tool in json.loads(texts(events))] == ["get_current_time", "read"]
    assert json.loads(texts(events))[0]["inputSchema"]["type"] == "object"


@pytest.mark.asyncio
async def test_an_agent_without_http_mcp_runs_with_its_own_tools_and_says_so(env):
    use_card_tools(env, "get_current_time")
    env.flags = []
    events = await env.collect("scenario:text")
    assert "cannot connect to K41 tools" in texts(events) and "Hello world" in texts(events)
    assert attached_servers(env) == []


@pytest.mark.asyncio
async def test_approved_call_runs_and_the_transcript_shows_the_real_result(env):
    use_card_tools(env, "get_current_time")
    events = await env.collect("scenario:mcp_call")
    assert "user_input_request" not in types(events)
    output = next(event for event in events if event["type"] == "tool_result")["content"]
    assert output[:4].isdigit() and texts(events) == output
    assert ("tool", output) in visible(await env.messages())


@pytest.mark.asyncio
async def test_duplicated_mcp_arguments_are_not_shown_twice(env):
    use_card_tools(env, "get_current_time")
    env.agent.tool_permissions = [{"action": "get_current_time", "effect": "ask"}]
    first = await env.collect("scenario:mcp_swapped_arguments")
    question = question_of(first)
    assert question["id"].startswith("permission:")
    assert "'arguments': {'a': 2}" in question["question"]


@pytest.mark.asyncio
async def test_denied_call_does_not_run(env):
    use_card_tools(env, "get_current_time")
    env.agent.tool_permissions = [{"action": "get_current_time", "effect": "ask"}]
    first = await env.collect("scenario:mcp_call")
    second = await env.collect(resume_payload=resume_k41(first, "deny"))
    assert "User denied get_current_time" in texts(second)
    assert mcp_choices(env) == ["allow"]


@pytest.mark.asyncio
async def test_surfaces_without_a_person_deny_card_tools(env):
    use_card_tools(env, "get_current_time")
    env.agent.tool_permissions = [{"action": "get_current_time", "effect": "ask"}]
    events = await env.collect("scenario:mcp_call", platform="telegram", thread="telegram_user_1")
    assert "approval_unavailable" in texts(events)
    assert "user_input_request" not in types(events)


@pytest.mark.asyncio
async def test_unreported_bridge_call_still_obeys_k41_deny(env):
    use_card_tools(env, "get_current_time")
    env.agent.tool_permissions = [{"action": "get_current_time", "effect": "deny"}]
    events = await env.collect("scenario:mcp_unapproved")
    assert "Policy denies get_current_time" in texts(events)


@pytest.mark.asyncio
async def test_an_approval_does_not_cover_other_arguments(env):
    use_card_tools(env, "get_current_time")
    env.agent.tool_permissions = [{"action": "get_current_time", "effect": "ask"}]
    first = await env.collect("scenario:mcp_swapped_arguments")
    assert "'a': 2" in question_of(first)["question"]
    second = await env.collect(resume_payload=resume_k41(first))
    assert texts(second)[:4].isdigit()


@pytest.mark.asyncio
async def test_auto_runs_without_asking_and_tells_the_agent_only_once(env):
    use_card_tools(env, "get_current_time", permission_mode="auto")
    events = await env.collect("scenario:mcp_call")
    assert "user_input_request" not in types(events)
    assert not texts(events).startswith("ERROR")
    assert mcp_choices(env) == ["allow"]


@pytest.mark.asyncio
async def test_allow_always_is_kept_by_k41_for_this_conversation(env):
    use_card_tools(env, "get_current_time")
    env.agent.tool_permissions = [{"action": "get_current_time", "effect": "ask"}]
    first = await env.collect("scenario:mcp_call_twice")
    second = await env.collect(resume_payload=resume_k41(first, "allow_thread"))
    assert "user_input_request" not in types(second)
    assert texts(second) == "True True" and mcp_choices(env) == ["allow", "allow"]


@pytest.mark.asyncio
async def test_a_grant_survives_the_idle_stop_of_the_agent_process(env):
    use_card_tools(env, "get_current_time")
    env.agent.tool_permissions = [{"action": "get_current_time", "effect": "ask"}]
    first = await env.collect("scenario:mcp_call")
    await env.collect(resume_payload=resume_k41(first, "allow_thread"))
    await env.service.restart_client(env.agent.acp.agent)
    events = await env.collect("scenario:mcp_call")
    assert "user_input_request" not in types(events) and not texts(events).startswith("ERROR")


@pytest.mark.asyncio
async def test_exact_grant_survives_a_server_restart(env):
    use_card_tools(env, "get_current_time")
    env.agent.tool_permissions = [{"action": "get_current_time", "effect": "ask"}]
    first = await env.collect("scenario:mcp_call")
    await env.collect(resume_payload=resume_k41(first, "allow_thread"))
    await env.restart_server()
    events = await env.collect("scenario:mcp_call")
    assert "user_input_request" not in types(events)


@pytest.mark.asyncio
async def test_control_tools_stay_out_of_reach(env):
    use_card_tools(env, "get_current_time", permission_mode="auto")
    events = await env.collect("scenario:mcp_control_tool")
    assert texts(events).startswith("ERROR Tool 'ask_user' is not available")


@pytest.mark.asyncio
async def test_the_endpoint_requires_the_session_token_and_post(env):
    use_card_tools(env, "get_current_time")
    assert (await env.collect("scenario:mcp_bad_token"))[0]["content"] == "status=401"
    assert (await env.collect("scenario:mcp_get"))[0]["content"] == "status=405"


@pytest.mark.asyncio
async def test_a_token_stops_working_after_a_tool_change_and_the_session_is_resumed(env):
    use_card_tools(env, "get_current_time")
    await env.collect("scenario:text")
    first_token = attached_servers(env)[0]["headers"][0]["value"]
    use_card_tools(env, "get_current_time", "html_preview")
    events = await env.collect("scenario:mcp_list")
    assert len(env.calls("session/new")) == 1
    second_token = attached_servers(env, "session/resume")[0]["headers"][0]["value"]
    assert second_token != first_token
    assert [tool["name"] for tool in json.loads(texts(events))] == ["get_current_time", "html_preview", "read"]
    assert first_token.removeprefix("Bearer ") not in env.service._tool_bridge._sessions


@pytest.mark.asyncio
async def test_unchanged_tools_do_not_resume_a_live_session(env):
    use_card_tools(env, "get_current_time")
    await env.collect("scenario:text")
    await env.collect("scenario:text")
    assert env.calls("session/resume") == []


@pytest.mark.asyncio
async def test_a_restart_attaches_a_fresh_endpoint_to_the_resumed_session(env):
    use_card_tools(env, "get_current_time")
    await env.collect("scenario:text")
    before = attached_servers(env)[0]
    await env.restart_server()
    events = await env.collect("scenario:mcp_list")
    after = attached_servers(env, "session/resume")[0]
    assert after["headers"] != before["headers"]
    assert "get_current_time" in [tool["name"] for tool in json.loads(texts(events))]


@pytest.mark.asyncio
async def test_a_restarted_endpoint_reaches_a_live_session_again(env):
    use_card_tools(env, "get_current_time")
    await env.collect("scenario:text")
    before = attached_servers(env)[0]["url"]
    bridge = env.service._tool_bridge
    bridge._server.should_exit = True
    await asyncio.wait_for(bridge._task, 10)
    events = await env.collect("scenario:mcp_list")
    assert attached_servers(env, "session/resume")[0]["url"] != before
    assert "get_current_time" in [tool["name"] for tool in json.loads(texts(events))]


@pytest.mark.asyncio
async def test_tools_are_listed_for_an_agent_that_connects_while_the_session_opens(env):
    use_card_tools(env, "get_current_time")
    env.flags = ["--mcp-http", "--mcp-on-new"]
    await env.collect("scenario:text")
    assert "get_current_time" in json.loads((env.state_dir / "mcp_tools_on_new.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_a_call_whose_input_the_agent_did_not_report_can_be_approved(env):
    use_card_tools(env, "get_current_time")
    env.agent.tool_permissions = [{"action": "get_current_time", "effect": "ask"}]
    first = await env.collect("scenario:mcp_call_without_input")
    second = await env.collect(resume_payload=resume_k41(first))
    assert texts(second)[:4].isdigit()


@pytest.mark.asyncio
async def test_the_endpoint_leaves_the_logging_of_the_host_server_alone(env):
    use_card_tools(env, "get_current_time")
    records = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    access, error = logging.getLogger("uvicorn.access"), logging.getLogger("uvicorn.error")
    handler = Collect()
    levels = access.level, error.level
    propagate = access.propagate
    access.addHandler(handler)
    access.setLevel(logging.INFO)
    error.setLevel(logging.INFO)
    access.propagate = True
    try:
        await env.collect("scenario:mcp_list")
        assert handler in access.handlers and access.propagate
        assert access.level == logging.INFO and error.level == logging.INFO
        assert records == []  # Bridge requests stay out of the host's access log.
    finally:
        access.removeHandler(handler)
        access.setLevel(levels[0])
        error.setLevel(levels[1])
        access.propagate = propagate


@pytest.mark.asyncio
async def test_a_finished_turn_leaves_nothing_on_its_session(env):
    use_card_tools(env, "get_current_time")
    await env.collect("scenario:text")
    [tools] = env.service._session_tools.values()
    assert tools.turn is None and tools.provider is None


def bridge_tokens(env):
    return set(env.service._tool_bridge._sessions)


@pytest.mark.asyncio
async def test_a_workspace_change_revokes_the_token_of_the_old_session(env, tmp_path):
    use_card_tools(env, "get_current_time")
    await env.collect("scenario:text")
    old_tokens = bridge_tokens(env)
    other = tmp_path / "other"
    other.mkdir()
    await env.collect("scenario:text", working_dir=other)
    assert len(env.service._session_tools) == 1
    assert not old_tokens & bridge_tokens(env) and len(bridge_tokens(env)) == 1


@pytest.mark.asyncio
async def test_a_session_that_cannot_be_restored_loses_its_token(env):
    use_card_tools(env, "get_current_time")
    await env.collect("scenario:text")
    old_tokens = bridge_tokens(env)
    await env.service.restart_client(env.agent.acp.agent)
    for marker in env.state_dir.glob("session-*"):
        marker.unlink()
    await env.collect("scenario:text")
    assert len(env.calls("session/new")) == 2 and len(env.service._session_tools) == 1
    assert not old_tokens & bridge_tokens(env) and len(bridge_tokens(env)) == 1


@pytest.mark.asyncio
async def test_deleting_a_conversation_releases_its_session(env, monkeypatch):
    use_card_tools(env, "get_current_time")
    await env.collect("scenario:text")
    assert bridge_tokens(env)
    monkeypatch.setattr("agent.modules.workflows.checkpoint.store.get_checkpointer", InMemorySaver)
    await delete_workflow_thread_tree(THREAD)
    assert not bridge_tokens(env) and not env.service._session_tools


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    ValueError("Missing required config 'model' for tool 'x'."),
    TypeError("Object of type Foo is not JSON serializable"),
    KeyError("function"),
])
async def test_a_tool_problem_only_costs_the_tools(env, monkeypatch, error):
    use_card_tools(env, "get_current_time")

    async def broken(self):
        raise error

    monkeypatch.setattr("agent.modules.workflows.graphs.acp_agent.CardToolProvider.tool_names", broken)
    events = await env.collect("scenario:text")
    assert "K41 tools are unavailable for this message" in texts(events) and "Hello world" in texts(events)


@pytest.mark.asyncio
async def test_stopping_a_turn_cancels_a_running_tool(env, monkeypatch):
    use_card_tools(env, "get_current_time", permission_mode="auto")
    started, stopped = asyncio.Event(), asyncio.Event()

    async def slow(self, name, arguments):
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            stopped.set()
            raise

    monkeypatch.setattr("agent.modules.workflows.graphs.acp_agent.CardToolProvider.call_tool", slow)
    run = asyncio.ensure_future(env.collect("scenario:mcp_call"))
    await asyncio.wait_for(started.wait(), 20)
    assert env.service.cancel_thread_now(THREAD)
    await asyncio.wait_for(stopped.wait(), 5)
    await asyncio.wait_for(run, 20)


# Browser handoff --------------------------------------------------------------


OWNER = "admin:1"
AFTER_SIGN_IN = "heading Signed in [1-1]"


class HandoffFacade:
    async def snapshot(self, **_values):
        return AFTER_SIGN_IN


class HandoffDriver:
    def __init__(self):
        self.facade = HandoffFacade()

    async def create_session(self, _artifacts):
        return HandoffDriver()

    async def close(self):
        pass

    async def tabs(self):
        return [{"id": "page", "url": "http://localhost", "title": "Login"}]


@pytest_asyncio.fixture
async def browser(env, isolated_container, tmp_path):
    metadata = MetaData()
    for model in (BrowserArtifact, BrowserBinding, BrowserInvocation, BrowserProfile, BrowserProjectProfile, ThreadWorkspace):
        model.__table__.to_metadata(metadata)
    await initialize_async_engine(metadata=metadata, container=isolated_container)

    async def launch(*_args):
        return HandoffDriver()

    service = BrowserService(isolated_container, root=tmp_path / "browser", launcher=launch)
    isolated_container._browser_service = service
    yield service
    await service.shutdown()
    await close_async_engine(container=isolated_container)


async def start_handoff(env, *, permission_mode="auto"):
    use_card_tools(env, "browser_handoff", permission_mode=permission_mode)
    return await env.collect("scenario:mcp_handoff", browser_owner_id=OWNER)


def handoff_request(events):
    return next(event for event in events if event["type"] == "browser_handoff_request")


def handoff_resume(events, decision="return", *, handoff_id=None):
    return {"action": "browser_handoff", "handoff_id": handoff_id or handoff_request(events)["handoff_id"], "decision": decision}


async def wait_until_released(browser):
    async def released():
        while browser.sessions or await browser.repository.pending_handoff(THREAD, include_expired=True):
            await asyncio.sleep(0.02)

    await asyncio.wait_for(released(), 10)


@pytest.mark.asyncio
async def test_handoff_pauses_the_turn_and_returns_the_result_to_the_agent(env, browser):
    first = await start_handoff(env)
    assert handoff_request(first)["reason"] == "Sign in"
    assert (await env.state()).next == ("acp_permission",)
    # The tool call is still waiting, and the viewer's status needs the same thread lock.
    status = await asyncio.wait_for(browser.status(THREAD, OWNER), 5)
    assert status["mode"] == "human" and status["handoff"]["handoff_id"] == handoff_request(first)["handoff_id"]

    second = await env.collect(resume_payload=handoff_resume(first), browser_owner_id=OWNER)
    result = json.loads(texts(second))
    assert result["handoff_status"] == "returned" and result["snapshot"] == AFTER_SIGN_IN
    assert "browser_handoff_settled" in types(second)
    assert not (await env.state()).next
    status = await browser.status(THREAD, OWNER)
    assert status["mode"] == "agent" and status["handoff"] is None


@pytest.mark.asyncio
async def test_handoff_is_asked_after_the_agent_gets_the_call_approved(env, browser):
    first = await start_handoff(env, permission_mode="ask")
    assert "browser_handoff_request" not in types(first) and not browser.sessions
    second = await env.collect(resume_payload=resume_k41(first), browser_owner_id=OWNER)
    third = await env.collect(resume_payload=handoff_resume(second), browser_owner_id=OWNER)
    assert json.loads(texts(third))["handoff_status"] == "returned"


@pytest.mark.asyncio
async def test_cancelling_a_handoff_tells_the_agent_and_closes_the_browser(env, browser):
    first = await start_handoff(env)
    second = await env.collect(resume_payload=handoff_resume(first, "cancel"), browser_owner_id=OWNER)
    assert json.loads(texts(second))["handoff_status"] == "cancelled"
    assert not browser.sessions and not (await env.state()).next


@pytest.mark.asyncio
@pytest.mark.parametrize("forged", [
    {"action": "browser_handoff", "handoff_id": "another", "decision": "return"},
    {"action": "acp_permission", "request_id": "another", "option_id": "allow"},
])
async def test_resume_for_something_else_does_not_end_a_handoff(env, browser, forged):
    first = await start_handoff(env)
    second = await env.collect(resume_payload=forged, browser_owner_id=OWNER)
    assert handoff_request(second)["handoff_id"] == handoff_request(first)["handoff_id"]
    assert (await browser.status(THREAD, OWNER))["mode"] == "human"


@pytest.mark.asyncio
async def test_expired_handoff_ends_the_tool_call_without_waiting_for_an_answer(env, browser, monkeypatch):
    monkeypatch.setattr(BrowserService, "idle_ttl", 1)
    first = await start_handoff(env)

    async def turn_ended():
        while env.service.agent_is_busy(env.agent.acp.agent):
            await asyncio.sleep(0.02)

    await asyncio.wait_for(turn_ended(), 10)
    assert not browser.sessions
    # The graph still waits, so the person can see the result by answering.
    assert (await env.state()).next == ("acp_permission",)
    assert (await browser.owned_handoff(THREAD, OWNER))["expired"] is True
    second = await env.collect(resume_payload=handoff_resume(first), browser_owner_id=OWNER)
    assert json.loads(texts(second))["handoff_status"] == "expired"
    assert await browser.owned_handoff(THREAD, OWNER) is None
    assert not (await env.state()).next


async def assert_stopped_handoff_was_committed(env):
    state = await env.state()
    assert not state.next and not any(task.interrupts for task in state.tasks)
    rows = visible(state.values["messages"])
    assert rows[:3] == [("user", "scenario:mcp_handoff"), ("call", "acp_other"), ("tool", "The tool call was cancelled.")]
    assert rows[3][0] == "ai"  # Output after the last commit survives as well.
    assert env.service.turn(state.values["acp_turn_id"]).committed


@pytest.mark.asyncio
async def test_stopping_the_turn_releases_the_browser(env, browser):
    await start_handoff(env)
    assert await env.service.stop_thread(THREAD) is True
    await wait_until_released(browser)
    await assert_stopped_handoff_was_committed(env)
    turn = env.service.turn((await env.state()).values["acp_turn_id"])
    assert "browser_handoff_settled" in types(turn.take_events())
    events = await env.collect("after the stop", browser_owner_id=OWNER)
    assert texts(events) == "after the stop"


@pytest.mark.asyncio
async def test_a_stop_from_synchronous_code_also_commits_a_handoff_turn(env, browser):
    await start_handoff(env)
    assert env.service.cancel_thread_now(THREAD) is True

    async def committed():
        while (await env.state()).next:
            await asyncio.sleep(0.02)

    await asyncio.wait_for(committed(), 10)
    await assert_stopped_handoff_was_committed(env)


@pytest.mark.asyncio
async def test_new_message_ends_the_handoff_of_the_old_turn(env, browser):
    await start_handoff(env)
    events = await env.collect("second message", browser_owner_id=OWNER)
    assert texts(events) == "second message"
    await wait_until_released(browser)


async def leave_handoff_behind(browser):
    """What a process that died during a handoff leaves in the database."""
    await browser.repository.record(THREAD, "old-call", "fingerprint", "handoff", {
        "type": "browser_handoff_request", "handoff_id": "old", "generation": "gone", "reason": "Sign in",
        "expires_at": time.time() + 600, "tool_call_id": "old-call", "thread_id": THREAD,
    })


@pytest.mark.asyncio
async def test_new_message_after_a_restart_clears_the_old_handoff(env, browser):
    await leave_handoff_behind(browser)
    await env.collect("hello", browser_owner_id=OWNER)
    assert await browser.owned_handoff(THREAD, OWNER) is None


@pytest.mark.asyncio
async def test_answering_a_handoff_after_a_restart_reports_a_lost_turn(env, browser):
    first = await start_handoff(env)
    await env.restart_server()
    await wait_until_released(browser)
    await leave_handoff_behind(browser)
    events = await env.collect(resume_payload=handoff_resume(first), browser_owner_id=OWNER)
    assert TURN_LOST_NOTICE in texts(events)
    assert await browser.owned_handoff(THREAD, OWNER) is None


@pytest.mark.asyncio
async def test_handoff_is_refused_where_nobody_can_answer(env, browser):
    use_card_tools(env, "browser_handoff", permission_mode="auto")
    events = await env.collect("scenario:mcp_handoff", platform="telegram", thread="telegram_user_1", browser_owner_id=OWNER)
    assert texts(events).startswith("ERROR Nobody can take over the browser")
    assert not browser.sessions


def test_handoff_stays_selected_on_a_card():
    config = AgentConfig(permission_mode="ask", name="ag", graph_type="acp_agent", tools=["browser_handoff", "ask_user"])
    assert config.tools == ["browser_handoff"]


def test_tool_output_keeps_text_and_images():
    from agent.modules.acp.tool_bridge import tool_output

    output = tool_output([{"type": "text", "text": "a"}, "b", {"type": "image", "base64": "QQ==", "mime_type": "image/jpeg"}])
    assert output.text == "a\nb" and output.images == (("QQ==", "image/jpeg"),) and not output.is_error
    assert tool_output("plain", is_error=True).is_error


def test_an_empty_tool_result_is_not_replaced_by_the_agents_report():
    transcript = TurnTranscript("turn")
    item = transcript.tool_item("m1")
    transcript.merge_tool(item, {"status": "completed", "rawOutput": "Call get_current_time tool"})
    item.bridged_output = ""
    assert transcript.tool_output(item) == ""


def test_acp_settings_are_only_defaulted_for_acp_cards():
    assert AgentConfig(permission_mode="ask", name="ag", graph_type="acp_agent").acp == AcpAgentSettings()
    assert AgentConfig(name="other", graph_type="react_agent").acp is None
