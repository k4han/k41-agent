import asyncio
import json
import time
from importlib import import_module
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import select

from agent.modules.admin_auth import get_current_admin
from agent.modules.agent_runtime import run_agent_protocol_stream
from agent.modules.agent_runtime.async_subagents.models import SubagentExecutionRecord
from agent.modules.agent_runtime.async_subagents.repository import job_dict
from agent.modules.agent_runtime.background_tasks import BackgroundTask, BackgroundTaskManager, TaskStatus
from agent.modules.notifications.interruption import InterruptionTimeoutManager, ResponseConflict
from agent.modules.notifications.models import InterruptionRecord, NotificationDelivery
from agent.modules.notifications.settings import NotificationSettings
from agent.modules.notifications.telegram import _apply, _prompt
from agent.modules.providers import ModelPoolExhausted
from agent.modules.tools.builtin.utility.ask_user import AskUserQuestion, _ask_user
from agent.modules.users.models import User, UserIdentity
from agent.modules.workflows.run_config import WorkflowContext
from agent.modules.workflows.state.base import BaseState
from agent.shared.infrastructure.db.base import Base
from test_async_subagents import admit, repository, runtime_environment
from test_scheduler_executions import browser, scheduled


ANSWER = {"action": "answer", "answers": [{"question_id": "q", "selected_option_ids": ["yes"]}]}


def install_graph(env, *nodes):
    builder = StateGraph(BaseState, context_schema=WorkflowContext)
    for node in nodes:
        builder.add_node(node.__name__, node)
    for source, target in pairwise((START, *(node.__name__ for node in nodes), END)):
        builder.add_edge(source, target)
    env.current_graph[0] = builder.compile(checkpointer=InMemorySaver())


async def install_notification_manager(env, monkeypatch, clock):
    session_factory = env.repository._session_factory
    async with await session_factory() as session, session.begin():
        connection = await session.connection()
        await connection.run_sync(lambda conn: Base.metadata.create_all(conn, tables=[
            User.__table__, UserIdentity.__table__, InterruptionRecord.__table__, NotificationDelivery.__table__,
        ]))
    manager = InterruptionTimeoutManager(env.container, clock=clock, session_factory=session_factory)
    monkeypatch.setattr(env.container, "_interruption_timeout_manager", manager)
    async with await manager.session_factory() as session, session.begin():
        user = User(is_active=True)
        session.add(user)
        await session.flush()
        identity = UserIdentity(platform="telegram", external_id="99", user_id=user.id)
        session.add(identity)
        await session.flush()
        identity_id = identity.id
    settings = NotificationSettings(recipient_identity_ids=[identity_id], dashboard_base_url="https://agent.example")
    monkeypatch.setattr(InterruptionTimeoutManager, "settings", property(lambda _: settings))
    return manager, settings, identity_id


@pytest_asyncio.fixture
async def notification_run(runtime_environment, monkeypatch):
    env = runtime_environment
    now = [1000.]
    manager, settings, identity_id = await install_notification_manager(env, monkeypatch, lambda: now[0])
    monkeypatch.setattr(env.service, "start", AsyncMock())
    questions = [AskUserQuestion(id="q", question="Continue?", options=[{"id": "yes", "label": "Yes"}])]

    async def work(state):
        response = _ask_user("Decision", questions, "", SimpleNamespace(tool_call_id="question"))
        return {"messages": [AIMessage(content=response)]}

    install_graph(env, work)

    async def pause(thread_id="api_parent"):
        _ = [event async for event in run_agent_protocol_stream(
            user_input="Run", thread_id=thread_id, agent_name="parent",
            browser_owner_id="admin:1", usage_context={"platform": "api"},
        )]
        async with await manager.session_factory() as session:
            return await session.scalar(select(InterruptionRecord).where(InterruptionRecord.thread_id == thread_id))

    return SimpleNamespace(env=env, manager=manager, now=now, pause=pause,
                           identity_id=identity_id, settings=settings, questions=questions)


@pytest_asyncio.fixture
async def notification_http(notification_run, monkeypatch):
    api = import_module("agent.delivery.http.api.router")
    events = import_module("agent.delivery.http.api.events_v2")
    env = notification_run.env
    monkeypatch.setattr(env.service, "start", type(env.service).start.__get__(env.service))

    async def prepare(request, browser_owner_id):
        return {"thread_id": request.thread_id, "user_input": request.message, "agent_name": "parent",
                "workflow": "react_agent", "browser_owner_id": browser_owner_id}

    monkeypatch.setattr(api, "prepare_run_params", prepare)
    monkeypatch.setattr(events, "prepare_run_params", prepare)
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(events.router)
    app.dependency_overrides[get_current_admin] = lambda: "1"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def deliveries(manager):
    async with await manager.session_factory() as session:
        return list(await session.scalars(select(NotificationDelivery).order_by(NotificationDelivery.id)))


@pytest.mark.asyncio
@pytest.mark.parametrize("visible", [False, True])
async def test_notice_respects_dashboard_presence(notification_run, visible):
    state = notification_run
    if visible:
        state.manager.mark_presence("admin:1", "api_parent", "tab", True)
    row = await state.pause()
    assert row.deadline == state.now[0] + 1800
    await state.manager.tick()
    assert len(await deliveries(state.manager)) == int(not visible)
    state.now[0] += 60
    await state.manager.tick()
    assert [item.event_type for item in await deliveries(state.manager)] == ["ask_user"]


@pytest.mark.asyncio
async def test_timeout_recovers_and_resumes_real_graph(notification_run):
    state = notification_run
    row = await state.pause()
    state.now[0] = row.deadline
    manager = InterruptionTimeoutManager(state.env.container, clock=lambda: state.now[0],
                                         session_factory=state.manager.session_factory)
    state.env.container._interruption_timeout_manager = manager
    await manager.tick()
    await manager.tick()
    assert [item.event_type for item in await deliveries(manager)] == ["interruption_timeout"]
    job = await state.env.repository.claim(row.key, "worker")
    assert job
    _ = [event async for event in state.env.service._job_stream(job, "continuation")]
    snapshot = await manager.snapshot(row)
    result = json.loads(snapshot.values["messages"][-1].content)
    assert result["type"] == "ask_user_timeout"
    assert "30" in result["content"]


@pytest.mark.asyncio
async def test_dispatcher_resumes_with_channels_disabled(notification_run, monkeypatch):
    state = notification_run
    state.settings.recipient_identity_ids.clear()
    state.settings.timeout_minutes = 1
    row = await state.pause()
    assert row.deadline == state.now[0] + 60
    monkeypatch.setattr(state.env.service, "start", type(state.env.service).start.__get__(state.env.service))
    state.now[0] = row.deadline
    await state.manager.tick()
    async with asyncio.timeout(6):
        stream = None
        while stream is None:
            stream = await state.env.container.chat_stream_manager.get_session(row.thread_id)
            if stream is None:
                await asyncio.sleep(0.02)
        await stream.task
    snapshot = await state.manager.snapshot(row)
    assert "ask_user_timeout" in snapshot.values["messages"][-1].content
    assert await deliveries(state.manager) == []


@pytest.mark.asyncio
async def test_competing_replies_enqueue_only_one_job(notification_run):
    state = notification_run
    row = await state.pause()
    payload = {"action": "answer", "answers": [{"question_id": "q", "selected_option_ids": ["yes"]}]}
    outcomes = await asyncio.gather(
        state.manager.accept(row, payload),
        state.manager.accept(row, payload, identity_id=state.identity_id, external_id="99"),
        return_exceptions=True,
    )
    assert sum(result is None for result in outcomes) == 1
    assert sum(isinstance(result, ResponseConflict) for result in outcomes) == 1
    async with await state.manager.session_factory() as session:
        jobs = list(await session.scalars(select(SubagentExecutionRecord)))
    assert len(jobs) == 1
    assert jobs[0].job_id == row.key
    state.now[0] = row.deadline
    await state.manager.tick()
    assert await deliveries(state.manager) == []


async def queue_wakeup(env):
    task = await admit(env.repository)
    child = await env.repository.claim(task["run_id"], "worker")
    await env.repository.finish(child, "completed", result="Ready")
    await env.repository.prepare_wakeups()
    wakeup, = await env.repository.candidates()
    assert wakeup["kind"] == "wakeup"
    return wakeup


@pytest.mark.asyncio
@pytest.mark.parametrize("timed_out", [False, True])
async def test_response_precedes_an_older_deferred_wakeup(notification_run, timed_out):
    state = notification_run
    row = await state.pause()
    wakeup = await queue_wakeup(state.env)
    assert not await state.env.service._parent_ready(wakeup)
    if timed_out:
        # Also exercise a wakeup whose earlier admission was released.
        claimed = await state.env.repository.claim(wakeup["job_id"], "worker")
        await state.env.repository.heartbeat(claimed, release=True)
        state.now[0] = row.deadline
        await state.manager.tick()
    else:
        await state.manager.accept(row, ANSWER)
    assert [job["job_id"] for job in await state.env.repository.candidates()] == [row.key]
    assert await state.env.repository.claim(wakeup["job_id"], "worker") is None
    job = await state.env.repository.claim(row.key, "worker")
    _ = [event async for event in state.env.service._job_stream(job, "continuation")]
    snapshot = await state.manager.snapshot(row)
    assert not snapshot.next
    assert json.loads(snapshot.values["messages"][-1].content)["type"] == (
        "ask_user_timeout" if timed_out else "ask_user_answer"
    )


@pytest.mark.asyncio
async def test_wakeup_can_resume_its_own_question(notification_run, notification_http):
    state = notification_run

    async def work(values):
        if any(message.additional_kwargs.get("async_wakeup_id") for message in values["messages"]):
            _ask_user("Decision", state.questions, "", SimpleNamespace(tool_call_id="question"))
        return {"messages": [AIMessage(content="Combined result")]}

    install_graph(state.env, work)
    assert await state.pause() is None
    wakeup = await queue_wakeup(state.env)
    job = await state.env.repository.claim(wakeup["job_id"], "worker")
    _ = [event async for event in state.env.service._job_stream(job, "wakeup")]
    assert await state.env.repository.pending_notifications("api_parent") == []
    async with await state.manager.session_factory() as session:
        row = await session.scalar(select(InterruptionRecord))
    assert row.job_id == wakeup["job_id"]
    assert await state.manager.is_current(row)

    response = await notification_http.post("/v2/api/chat/events", json={
        "message": "", "thread_id": row.thread_id, "resume_payload": ANSWER, "interrupt_id": row.interrupt_id,
    })
    assert response.status_code == 200
    assert "Combined result" in response.text
    assert not (await state.manager.snapshot(row)).next


@pytest.mark.asyncio
async def test_dashboard_queue_drains_after_a_managed_response(notification_run, notification_http):
    state = notification_run
    thread_id = "api_parent"

    async def work(values):
        latest = next(message for message in reversed(values["messages"]) if isinstance(message, HumanMessage))
        if latest.content == "Requires an answer":
            _ask_user("Decision", state.questions, "", SimpleNamespace(tool_call_id="question"))
        return {"messages": [AIMessage(content=f"Completed {latest.content}")]}

    install_graph(state.env, work)
    endpoint = "/v2/api/chat/events"
    payload = {"thread_id": thread_id, "user_id": "dashboard", "enable_message_queue": True}
    start = await notification_http.post(endpoint, json={**payload, "message": "Requires an answer"})
    assert start.status_code == 200
    queue_path = f"/v2/api/chat/{thread_id}/message-queue"
    queued = await notification_http.post(queue_path, json={
        "id": "follow-up", "run_id": start.headers["X-Run-ID"], "text": "Follow-up",
    })
    assert queued.status_code == 202 and queued.json()["status"] == "waiting"

    resumed = await notification_http.post(endpoint, json={**payload, "message": "", "resume_payload": ANSWER})
    assert resumed.status_code == 200
    assert "Completed Requires an answer" in resumed.text
    assert "Completed Follow-up" in resumed.text
    snapshot = (await notification_http.get(queue_path)).json()
    assert snapshot["status"] == "idle" and snapshot["messages"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["permission", "approve"])
async def test_followup_approval_resumes_the_same_execution(notification_run, notification_http, monkeypatch, action):
    state = notification_run
    request = ({"type": "permission_request", "request_id": "write", "action": "write_file",
                "resource": "document.txt"} if action == "permission" else {"type": "plan_review", "plan": "Plan"})
    decision = ({"action": "permission", "request_id": "write", "decision": "allow_once"}
                if action == "permission" else {"action": "approve", "target_agent": "worker"})
    catalog = state.env.container.agent_catalog_service
    monkeypatch.setattr(catalog, "get_agent_card", lambda _: SimpleNamespace(
        valid=True, internal=False, plan_approval_targets=["worker"],
    ), raising=False)

    async def ask(values):
        _ask_user("Decision", state.questions, "", SimpleNamespace(tool_call_id="question"))
        return {"messages": [AIMessage(content="Initial question answered")]}

    async def approval(values, runtime):
        assert interrupt(request) == decision
        return {"messages": [AIMessage(content=f"Approved by {runtime.context.agent_name}")]}

    install_graph(state.env, ask, approval)
    row = await state.pause()
    endpoint = "/v2/api/chat/events"
    payload = {"message": "", "thread_id": row.thread_id}
    started = await notification_http.post(endpoint, json={**payload, "resume_payload": ANSWER})
    assert started.status_code == 200
    assert (await state.manager.snapshot(row)).next == ("approval",)
    response = await notification_http.post(endpoint, json={**payload, "resume_payload": decision})
    assert response.status_code == 200
    agent_name = "worker" if action == "approve" else "parent"
    assert f"Approved by {agent_name}" in response.text
    async with await state.manager.session_factory() as session:
        execution = await session.scalar(select(SubagentExecutionRecord))
    assert execution.job_id == row.key
    assert execution.status == "completed"
    assert job_dict(execution)["context"]["agent_name"] == agent_name


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/api/chat", "/v1/api/chat/stream"])
async def test_v1_chat_returns_a_managed_resume_result(notification_run, notification_http, path):
    state = notification_run

    async def work(values):
        _ask_user("Decision", state.questions, "", SimpleNamespace(tool_call_id="question"))
        return {"messages": [AIMessage(content="Managed reply")]}

    install_graph(state.env, work)
    row = await state.pause()
    response = await notification_http.post(path, json={
        "message": "", "thread_id": row.thread_id, "resume_payload": ANSWER,
    })
    assert response.status_code == 200
    assert (response.text if path.endswith("/stream") else response.json()["response"]) == "Managed reply"


@pytest.mark.asyncio
async def test_v1_sync_preserves_model_pool_exhaustion_after_resume(notification_run, notification_http):
    state = notification_run
    exhausted = ModelPoolExhausted("test-pool", 1800)

    async def work(values):
        _ask_user("Decision", state.questions, "", SimpleNamespace(tool_call_id="question"))
        raise exhausted

    install_graph(state.env, work)
    row = await state.pause()
    response = await notification_http.post("/v1/api/chat", json={
        "message": "", "thread_id": row.thread_id, "resume_payload": ANSWER,
    })
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": exhausted.code, "message": str(exhausted), "pool_name": exhausted.pool_name, "retry_at": exhausted.retry_at,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/api/chat", "/v1/api/chat/stream"])
async def test_v1_rejects_new_message_without_closing_queued_handoff(scheduled, monkeypatch, path):
    from agent.modules.tools.builtin import browser as tools

    state = scheduled
    manager, _, _ = await install_notification_manager(state.env, monkeypatch, time.time)
    thread = "api_parent"

    async def handoff(values, config, runtime):
        result = await tools._handoff(SimpleNamespace(config=config, context=runtime.context,
            tool_call_id="handoff"), "Wait for user")
        return {"messages": [AIMessage(content=result.content)]}

    install_graph(state.env, handoff)
    _ = [event async for event in run_agent_protocol_stream(
        user_input="Start", thread_id=thread, agent_name="parent",
        browser_owner_id="admin:1", usage_context={"platform": "api"},
    )]
    async with await manager.session_factory() as session:
        row = await session.scalar(select(InterruptionRecord))
    current = state.browser.sessions[thread]
    await manager.accept(row, {"action": "browser_handoff", "handoff_id": current.handoff_id, "decision": "return"})

    api = import_module("agent.delivery.http.api.router")
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_current_admin] = lambda: "1"
    monkeypatch.setattr(api, "prepare_run_params", AsyncMock(return_value={
        "thread_id": thread, "user_input": "New message", "agent_name": "parent", "browser_owner_id": "admin:1",
    }))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path, json={"message": "New message", "thread_id": thread})

    assert response.status_code == 409
    assert state.browser.sessions[thread] is current and not current.closed
    assert current.handoff_id == json.loads(row.request_json)["handoff_id"]


@pytest.mark.asyncio
async def test_scheduled_handoff_accepts_identical_retry(scheduled, monkeypatch):
    from agent.modules.scheduler.executions import resume_run, waiting_run

    state = scheduled
    await install_notification_manager(state.env, monkeypatch, time.time)
    await state.admit()
    await state.execute()
    service = state.env.service
    pending, = (await waiting_run(service, "scheduled-run-1", "admin:1"))["interrupts"]
    payload = {"action": "browser_handoff", "handoff_id": pending["value"]["handoff_id"], "decision": "return"}
    assert (await resume_run(service, "run-1", pending["id"], payload, "admin:1"))["status"] == "pending"
    assert (await resume_run(service, "run-1", pending["id"], payload, "admin:1"))["status"] == "pending"
    assert await state.env.repository.claim("run-1", "worker")
    assert (await resume_run(service, "run-1", pending["id"], payload, "admin:1"))["status"] == "running"
    with pytest.raises(ResponseConflict):
        await resume_run(service, "run-1", pending["id"], {**payload, "decision": "cancel"}, "admin:1")
    with pytest.raises(LookupError):
        await resume_run(service, "run-1", pending["id"], payload, "admin:2")


@pytest.mark.asyncio
async def test_async_task_accepts_identical_retry(notification_run):
    env = notification_run.env
    task = await admit(env.repository)
    job = await env.repository.claim(task["run_id"], "worker")
    _ = [event async for event in env.service._job_stream(job, "child-stream")]
    parent, task_id, run_id = task["parent_thread_id"], task["task_id"], task["run_id"]
    pending, = (await env.service.get(parent, task_id))["interrupts"]
    accepted = await env.service.resume(parent, task_id, run_id, pending["id"], ANSWER)
    replay = await env.service.resume(parent, task_id, run_id, pending["id"], ANSWER)
    assert accepted == replay and replay["status"] == "pending"
    with pytest.raises(ResponseConflict):
        await env.service.resume(parent, task_id, run_id, pending["id"], {**ANSWER, "summary": "Different"})
    with pytest.raises(ResponseConflict):
        await env.service.resume("another_parent", task_id, run_id, pending["id"], ANSWER)


@pytest.mark.asyncio
async def test_telegram_draft_supports_choices_and_replies(notification_run, monkeypatch):
    pytest.importorskip("aiogram")
    from agent.modules.channels.telegram.adapter import get_telegram_adapter

    state = notification_run
    state.questions[:] = [
        AskUserQuestion(id="q", question="Choose", selection_mode="multiple",
                        options=[{"id": "a", "label": "Alpha"}, {"id": "b", "label": "Beta"}]),
        AskUserQuestion(id="detail", question="Explain", free_text={"required": True}),
    ]
    await state.pause()
    await state.manager.tick()
    bot = SimpleNamespace(id=42, send_message=AsyncMock(side_effect=[
        SimpleNamespace(chat=SimpleNamespace(id=99), message_id=11),
        SimpleNamespace(chat=SimpleNamespace(id=99), message_id=12),
    ]))
    monkeypatch.setattr(get_telegram_adapter(), "_bot", bot)
    await state.manager.deliver()
    assert bot.send_message.await_count == 2
    assert bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard[-1][0].url == (
        "https://agent.example/chat?thread_id=api_parent"
    )
    assert (await deliveries(state.manager))[0].status == "sent"

    async def apply(message_id, **response):
        prompt = await _prompt(state.manager, bot_id=42, chat_id=99, message_id=message_id, external_id="99")
        return (await _apply(state.manager, prompt, "99", **response))[0]

    assert await apply(12, action="s") == "incomplete"
    assert await apply(11, action="0") == "saved"
    assert await apply(11, action="1") == "saved"
    assert await apply(12, reply="Details") == "saved"
    assert await apply(12, action="s") == "accepted"
    assert await apply(12, action="s") == "closed"
    async with await state.manager.session_factory() as session:
        queued = await session.scalar(select(SubagentExecutionRecord))
    answers = json.loads(queued.resume_json)["payload"]["answers"]
    assert answers[0]["selected_option_ids"] == ["a", "b"]
    assert answers[1]["custom_text"] == "Details"


@pytest.mark.asyncio
@pytest.mark.parametrize("multiple", [False, True])
async def test_telegram_callback_keeps_the_answer_when_message_edit_fails(notification_run, monkeypatch, multiple):
    pytest.importorskip("aiogram")
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditMessageReplyMarkup, EditMessageText

    from agent.modules.channels.telegram.adapter import get_telegram_adapter
    from agent.modules.notifications.settings import messages
    from agent.modules.notifications.telegram import handle_callback

    state = notification_run
    if multiple:
        state.questions[0] = state.questions[0].model_copy(update={"selection_mode": "multiple"})
    row = await state.pause()
    await state.manager.tick()
    text_edit = EditMessageText(chat_id=99, message_id=11, text="Saved")
    markup_edit = EditMessageReplyMarkup(chat_id=99, message_id=11)
    message = SimpleNamespace(
        chat=SimpleNamespace(id=99, type="private"), message_id=11, reply_markup=None,
        edit_text=AsyncMock(side_effect=TelegramBadRequest(method=text_edit, message="message is not modified")),
        edit_reply_markup=AsyncMock(side_effect=TelegramBadRequest(method=markup_edit, message="message is not modified")),
    )
    bot = SimpleNamespace(id=42, send_message=AsyncMock(return_value=message))
    monkeypatch.setattr(get_telegram_adapter(), "_bot", bot)
    await state.manager.deliver()
    message.reply_markup = bot.send_message.await_args.kwargs["reply_markup"]
    query = SimpleNamespace(bot=bot, message=message, from_user=SimpleNamespace(id=99), answer=AsyncMock(),
                            data=message.reply_markup.inline_keyboard[0][0].callback_data)

    await handle_callback(query)

    query.answer.assert_awaited_once_with(messages()["saved" if multiple else "accepted"])
    if multiple:
        message.edit_reply_markup.assert_awaited_once()
        delivery, = await deliveries(state.manager)
        assert json.loads(delivery.draft_json)["q"]["selected_option_ids"] == ["yes"]
    else:
        message.edit_text.assert_awaited_once()
        job, = await state.env.repository.candidates()
        assert job["job_id"] == row.key


@pytest.mark.asyncio
async def test_telegram_continuation_delivers_the_final_reply(notification_run, monkeypatch):
    from agent.modules.agent_runtime import runner
    from agent.modules.channels.agent_bridge import stream_agent_response
    from agent.modules.channels.contracts import InboundMessage
    from agent.modules.notifications import service
    from agent.modules.usage import build_usage_context

    state = notification_run
    thread_id = "telegram_99:99"
    usage = {"platform": "telegram", "user_id": "99", "channel_id": "99"}
    monkeypatch.setattr(runner, "load_usage_context", AsyncMock(return_value=build_usage_context(thread_id, usage)))
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "send_notification", send)

    async def work(values):
        _ask_user("Decision", state.questions, "", SimpleNamespace(tool_call_id="question"))
        return {"messages": [AIMessage(content="Final channel reply")]}

    install_graph(state.env, work)
    reply = AsyncMock(return_value=1)
    await stream_agent_response(
        InboundMessage(platform="telegram", user_id="99", channel_id="99", text="Run", is_private=True, reply=reply),
        {"user_input": "Run", "thread_id": thread_id, "agent_name": "parent", "usage_context": usage},
    )
    assert reply.await_args.args[0].text == "Waiting for your input."
    async with await state.manager.session_factory() as session:
        row = await session.scalar(select(InterruptionRecord))
    await state.manager.accept(row, ANSWER, identity_id=state.identity_id, external_id="99")
    job = await state.env.repository.claim(row.key, "worker")
    _ = [event async for event in state.env.service._job_stream(job, "continuation")]
    send.assert_awaited_once_with("telegram", "99", "Final channel reply", channel_id="99", mode="markdown")
    assert await state.env.repository.claim(row.key, "retry") is None


@pytest.mark.asyncio
async def test_channel_reply_targets_a_discord_channel(monkeypatch):
    from agent.modules import channels
    from agent.modules.channels.discord.adapter import DiscordChannelAdapter
    from agent.modules.notifications import send_notification

    outbound = SimpleNamespace(send=AsyncMock())
    client = SimpleNamespace(get_channel=lambda _: None, fetch_channel=AsyncMock(return_value=outbound))
    adapter = DiscordChannelAdapter()
    adapter.set_client(client)
    monkeypatch.setattr(channels, "load_channel_adapter", lambda _: adapter)
    monkeypatch.setattr(channels, "register_builtin_channel_adapters", lambda: None)

    assert await send_notification("discord", "99", "Final reply", channel_id="42")
    client.fetch_channel.assert_awaited_once_with(42)
    outbound.send.assert_awaited_once_with("Final reply")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["destination", "fetch", "send"])
async def test_discord_channel_reply_returns_false_on_failure(failure):
    from agent.modules.channels import OutboundMessage
    from agent.modules.channels.discord.adapter import DiscordChannelAdapter

    channel = SimpleNamespace(send=AsyncMock(side_effect=OSError("Could not send")))
    fetch = AsyncMock(side_effect=OSError("Could not fetch"))
    client = SimpleNamespace(get_channel=lambda _: channel if failure == "send" else None, fetch_channel=fetch)
    adapter = DiscordChannelAdapter()
    adapter.set_client(client)

    assert not await adapter.send_to_channel("invalid" if failure == "destination" else "42", OutboundMessage(text="Reply"))
    assert fetch.await_count == (1 if failure == "fetch" else 0)
    assert channel.send.await_count == (1 if failure == "send" else 0)


@pytest.mark.asyncio
async def test_stopping_an_active_resume_cancels_the_background_task(notification_run, monkeypatch):
    from agent.delivery.http.dashboard.routes.dashboard import stop_dashboard_session

    state = notification_run
    reached = asyncio.Event()
    monkeypatch.setattr(state.env.service, "start", type(state.env.service).start.__get__(state.env.service))

    async def work(values):
        _ask_user("Decision", state.questions, "", SimpleNamespace(tool_call_id="question"))
        reached.set()
        await asyncio.Event().wait()

    install_graph(state.env, work)
    row = await state.pause()
    tasks = state.env.container.task_manager
    cleanup = AsyncMock()
    task = BackgroundTask(task_id="paused-task", request="Run", agent_name="parent", thread_id=row.thread_id,
                          status=TaskStatus.WAITING, cleanup_hook=cleanup)
    tasks._tasks[task.task_id] = task
    persist = AsyncMock()
    monkeypatch.setattr(tasks, "_persist_task", persist)

    await state.manager.accept(row, ANSWER)
    async with asyncio.timeout(5):
        await reached.wait()
        registry = state.env.container.active_session_registry
        session_id = next(item["session_id"] for item in registry.list_active() if item["thread_id"] == row.thread_id)
        assert await stop_dashboard_session({"session_id": session_id}) == {"success": True}
    assert task.status is TaskStatus.CANCELLED
    persist.assert_awaited_once_with(task)
    cleanup.assert_awaited_once_with(task)
    async with await state.manager.session_factory() as session:
        execution = await session.scalar(select(SubagentExecutionRecord))
    assert execution.status == "cancelled"


@pytest.mark.asyncio
async def test_restore_reconciles_waiting_task_with_completed_resume(notification_run, monkeypatch):
    state = notification_run
    row = await state.pause()
    task = BackgroundTask(task_id="paused-task", request="Run", agent_name="parent",
                          thread_id=row.thread_id, status=TaskStatus.WAITING)
    repo = SimpleNamespace(list=AsyncMock(return_value=[task.to_dict()]), upsert=AsyncMock())
    monkeypatch.setattr("agent.modules.agent_runtime.repository.get_background_task_repository", lambda: repo)

    before = BackgroundTaskManager()
    await before.restore_from_persistence()
    assert before.get(task.task_id)["status"] == "waiting"
    repo.upsert.assert_not_awaited()

    async def interrupt_settlement(*args, **kwargs):
        raise RuntimeError("Interrupted while writing task history")

    monkeypatch.setattr(state.env.container.task_manager, "settle_paused", interrupt_settlement)
    await state.manager.accept(row, ANSWER)
    job = await state.env.repository.claim(row.key, "worker")
    _ = [event async for event in state.env.service._job_stream(job, "resume-stream")]
    graph = state.env.current_graph[0]
    await graph.aupdate_state({"configurable": {"thread_id": row.thread_id}},
                             {"messages": [AIMessage(content="Later message")]})

    after = BackgroundTaskManager()
    await after.restore_from_persistence()
    recovered = after.get(task.task_id)
    assert recovered["status"] == "completed"
    assert json.loads(recovered["result"])["type"] == "ask_user_answer"
    assert recovered["completed_at"] is not None
    repo.upsert.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_to_prompt", [True, False], ids=["reply", "plain-text"])
async def test_scheduled_question_reaches_telegram_with_local_dashboard(scheduled, monkeypatch, reply_to_prompt):
    pytest.importorskip("aiogram")
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import SendMessage
    from aiogram.types import ForceReply, InlineKeyboardMarkup

    from agent.modules.channels.telegram import adapter
    from agent.modules.notifications.settings import messages
    from agent.modules.scheduler.executions import get_run

    state = scheduled
    manager, settings, _ = await install_notification_manager(state.env, monkeypatch, lambda: 1000.)
    settings.dashboard_base_url = "http://localhost:8000"
    question = AskUserQuestion(id="name", question="What name should I use?", free_text={"required": True})

    async def work(values):
        response = _ask_user("Name", [question], "", SimpleNamespace(tool_call_id="question"))
        return {"messages": [AIMessage(content=response)]}

    bot_user = SimpleNamespace(id=42)
    chat = SimpleNamespace(id=99, type="private")
    original = SimpleNamespace(chat=chat, message_id=11, from_user=bot_user, reply_markup=None, edit_text=AsyncMock())

    async def send_message(**kwargs):
        markup = kwargs["reply_markup"]
        if isinstance(markup, InlineKeyboardMarkup) and any(button.url for row in markup.inline_keyboard for button in row):
            raise TelegramBadRequest(method=SendMessage(**kwargs), message="Inline keyboard button Wrong HTTP URL")
        return original

    bot = SimpleNamespace(id=bot_user.id, send_message=AsyncMock(side_effect=send_message))
    monkeypatch.setattr(adapter.get_telegram_adapter(), "_bot", bot)
    chat_handler = AsyncMock()
    monkeypatch.setattr(adapter, "process_inbound_message", chat_handler)
    install_graph(state.env, work)
    await state.admit(platform="telegram", user_id="99")
    await state.execute()
    await manager.tick()
    await manager.deliver()

    delivery, = await deliveries(manager)
    assert (delivery.event_type, delivery.status) == ("ask_user", "sent")
    bot.send_message.assert_awaited_once()
    assert question.question in bot.send_message.await_args.kwargs["text"]
    assert isinstance(bot.send_message.await_args.kwargs["reply_markup"], ForceReply)
    assert (await get_run(state.env.service, "run-1", "admin:1"))["status"] == "waiting"

    reply = SimpleNamespace(
        bot=bot, chat=chat, reply_to_message=original if reply_to_prompt else None,
        from_user=SimpleNamespace(id=99), text="Alice", answer=AsyncMock(),
    )
    await adapter.handle_telegram_message(reply)
    chat_handler.assert_not_awaited()
    original.edit_text.assert_not_awaited()
    reply.answer.assert_awaited_once_with(messages()["accepted"], parse_mode=None)
    await state.execute()
    assert (await get_run(state.env.service, "run-1", "admin:1"))["status"] == "completed"
    state.notify.assert_awaited_once()
    assert "Alice" in state.notify.await_args.args[2]


@pytest.mark.asyncio
async def test_scheduled_text_questions_resume_after_all_replies(scheduled, monkeypatch):
    pytest.importorskip("aiogram")
    from aiogram.types import ForceReply

    from agent.modules.channels.telegram import adapter
    from agent.modules.notifications.settings import messages
    from agent.modules.scheduler.executions import get_run

    state = scheduled
    manager, settings, _ = await install_notification_manager(state.env, monkeypatch, lambda: 1000.)
    settings.dashboard_base_url = "http://localhost:8000"
    questions = [
        AskUserQuestion(id=field, question=f"Enter the {field}.", free_text={"required": True})
        for field in ("name", "city", "color")
    ]

    async def work(values):
        response = _ask_user("Details", questions, "", SimpleNamespace(tool_call_id="questions"))
        return {"messages": [AIMessage(content=response)]}

    chat = SimpleNamespace(id=99, type="private")
    bot_user = SimpleNamespace(id=42)
    originals = [SimpleNamespace(chat=chat, message_id=index, from_user=bot_user, reply_markup=None)
                 for index in range(11, 14)]
    bot = SimpleNamespace(id=bot_user.id, send_message=AsyncMock(side_effect=originals))
    monkeypatch.setattr(adapter.get_telegram_adapter(), "_bot", bot)
    chat_handler = AsyncMock()
    monkeypatch.setattr(adapter, "process_inbound_message", chat_handler)
    install_graph(state.env, work)
    await state.admit(platform="telegram", user_id="99")
    await state.execute()
    await manager.tick()
    await manager.deliver()
    assert bot.send_message.await_count == 3
    assert all(isinstance(call.kwargs["reply_markup"], ForceReply) for call in bot.send_message.await_args_list)

    for index, text in ((2, "Green"), (0, "Alice"), (1, "Paris")):
        reply = SimpleNamespace(bot=bot, chat=chat, from_user=SimpleNamespace(id=99),
                                reply_to_message=originals[index], text=text, answer=AsyncMock())
        await adapter.handle_telegram_message(reply)
        status = "accepted" if index == 1 else "saved"
        reply.answer.assert_awaited_once_with(messages()[status], parse_mode=None)
        run = await get_run(state.env.service, "run-1", "admin:1")
        assert run["status"] == ("pending" if index == 1 else "waiting")

    answers = run["resume"]["payload"]["answers"]
    assert {answer["question_id"]: answer["custom_text"] for answer in answers} == {
        "name": "Alice", "city": "Paris", "color": "Green",
    }
    reply.answer.reset_mock()
    await adapter.handle_telegram_message(reply)
    reply.answer.assert_awaited_once_with(messages()["closed"], parse_mode=None)
    chat_handler.assert_not_awaited()
    await state.execute()
    assert (await get_run(state.env.service, "run-1", "admin:1"))["status"] == "completed"
    state.notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_telegram_plain_text_requires_an_exact_reply_when_multiple_questions_wait(notification_run, monkeypatch):
    pytest.importorskip("aiogram")
    from agent.modules.channels.telegram import adapter
    from agent.modules.notifications.settings import messages

    state = notification_run
    first = await state.pause()
    await state.pause("api_other")
    await state.manager.tick()
    bot_user = SimpleNamespace(id=42)
    chat = SimpleNamespace(id=99, type="private")
    originals = [SimpleNamespace(message_id=number, chat=chat, from_user=bot_user, edit_text=AsyncMock())
                 for number in (11, 12)]
    bot = SimpleNamespace(id=bot_user.id, send_message=AsyncMock(side_effect=originals))
    monkeypatch.setattr(adapter.get_telegram_adapter(), "_bot", bot)
    chat_handler = AsyncMock()
    monkeypatch.setattr(adapter, "process_inbound_message", chat_handler)
    await state.manager.deliver()
    for original, call in zip(originals, bot.send_message.await_args_list, strict=True):
        original.reply_markup = call.kwargs["reply_markup"]

    reply = SimpleNamespace(bot=bot, chat=chat, from_user=SimpleNamespace(id=99),
                            reply_to_message=None, text="Alice", answer=AsyncMock())
    await adapter.handle_telegram_message(reply)
    chat_handler.assert_not_awaited()
    reply.answer.assert_awaited_once_with(messages()["ambiguous_reply"], parse_mode=None)
    assert await state.env.repository.candidates() == []

    first_delivery = next(delivery for delivery in await deliveries(state.manager)
                          if delivery.interruption_key == first.key)
    _, message_id = json.loads(first_delivery.telegram_json)["messages"]["0"]
    first_prompt = next(original for original in originals if original.message_id == message_id)
    reply.text = "/help"
    for target in (None, first_prompt):
        reply.reply_to_message = target
        await adapter.handle_telegram_message(reply)
        assert chat_handler.await_args.args[0].text == "/help"

    reply.text = "Alice"
    await adapter.handle_telegram_message(reply)
    assert chat_handler.await_count == 2
    first_prompt.edit_text.assert_awaited_once()
    assert [job["job_id"] for job in await state.env.repository.candidates()] == [first.key]


@pytest.mark.asyncio
async def test_scheduled_timeout_returns_live_browser(scheduled, monkeypatch):
    from agent.modules.browser import service as browser_service
    from agent.modules.scheduler.executions import get_run, resume_run, waiting_run

    state = scheduled
    now = [time.time()]
    manager = InterruptionTimeoutManager(state.env.container, clock=lambda: now[0],
                                          session_factory=state.env.repository._session_factory)
    async with await manager.session_factory() as session, session.begin():
        conn = await session.connection()
        await conn.run_sync(lambda db: Base.metadata.create_all(db, tables=[
            InterruptionRecord.__table__, NotificationDelivery.__table__,
        ]))
    monkeypatch.setattr(state.env.container, "_interruption_timeout_manager", manager)
    monkeypatch.setattr(browser_service, "time", SimpleNamespace(time=lambda: now[0], monotonic=time.monotonic))
    await state.admit()
    await state.execute()
    browser_session = state.browser.sessions["scheduled-run-1"]
    pending, = (await waiting_run(state.env.service, "scheduled-run-1", "admin:1"))["interrupts"]
    now[0] = browser_session.handoff_expires_at
    await manager.tick()
    with pytest.raises(ResponseConflict):
        await resume_run(state.env.service, "run-1", pending["id"], {
            "action": "browser_handoff", "handoff_id": browser_session.handoff_id, "decision": "return",
        }, "admin:1")
    await state.browser._expire_handoffs("scheduled-run-1")
    assert state.browser.sessions["scheduled-run-1"] is browser_session
    await state.execute()
    assert (await get_run(state.env.service, "run-1", "admin:1"))["status"] == "completed"
    assert browser_session.handoff_id is None
    assert browser_session.closed is False


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "resumed"), [(None, False), ("waiting", False), ("timed_out", False), ("timed_out", True)])
async def test_out_of_graph_handoff_has_a_bounded_wait_at_expiry(scheduled, monkeypatch, status, resumed):
    from agent.modules.browser import repository as browser_repository, service as browser_service

    now = [time.time()]
    manager, _, _ = await install_notification_manager(scheduled.env, monkeypatch, lambda: now[0])
    clock = SimpleNamespace(time=lambda: now[0], monotonic=time.monotonic)
    monkeypatch.setattr(browser_repository, "time", clock)
    monkeypatch.setattr(browser_service, "time", clock)
    monkeypatch.setattr(browser_service, "HANDOFF_RESUME_GRACE_SECONDS", 0.05)
    browser = scheduled.browser
    thread_id = "out-of-graph-handoff"
    open_handoff = browser._open_handoff
    decision_ready = asyncio.Event()

    async def browser_status(*args):
        if resumed:
            decision_ready.set()
        return status

    monkeypatch.setattr(manager, "browser_status", browser_status)

    async def expire_on_open(*args):
        result, payload, receipt = await open_handoff(*args)
        assert payload["timeout_minutes"]
        now[0] = payload["expires_at"] + browser_service.HANDOFF_EXPIRY_GRACE_SECONDS
        return result, payload, receipt

    async def wait_for_human(payload):
        await decision_ready.wait()
        return "return"

    monkeypatch.setattr(browser, "_open_handoff", expire_on_open)
    result = await asyncio.wait_for(browser.handoff(
        thread_id, "admin:1", "call", "Review", wait_for_human=wait_for_human,
    ), timeout=2)
    if resumed:
        assert result["handoff_status"] == "returned"
        assert result["timed_out"] is True
        assert browser.sessions[thread_id].handoff_id is None
    else:
        assert result["handoff_status"] == "expired"
        assert thread_id not in browser.sessions


@pytest.mark.asyncio
async def test_rate_alert_is_classified_and_deduplicated(notification_run, monkeypatch):
    from agent.modules.notifications import service

    state = notification_run
    async with await state.manager.session_factory() as session, session.begin():
        initial = await session.get(UserIdentity, state.identity_id)
        identities = [UserIdentity(platform=platform, external_id="88", user_id=initial.user_id)
                      for platform in ("discord", "zalo")]
        session.add_all(identities)
        await session.flush()
        state.settings.recipient_identity_ids.extend(identity.id for identity in identities)
    request = httpx.Request("POST", "https://provider.example")
    exception = httpx.HTTPStatusError("Too many requests", request=request,
                                      response=httpx.Response(429, request=request))
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "send_notification", send)
    await state.manager.publish_error("execution", "Scheduler", "api_parent", error=exception)
    await state.manager.publish_error("execution", "Scheduler", "api_parent", error=exception)
    await state.manager.deliver()
    assert [item.event_type for item in await deliveries(state.manager)] == ["rate_limit"] * 3
    assert {call.args[0] for call in send.await_args_list} == {"telegram", "discord", "zalo"}
    assert all("/chat?thread_id=api_parent" in call.args[2] for call in send.await_args_list)
