import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from queue import Queue
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from agent.modules.tools.builtin.shell.session_tools import bash, bash_close
from agent.modules.tools.builtin.shell.session_manager import (
    TerminalSession,
    TerminalSessionManager,
)
from agent.modules.agent_runtime.active_sessions import current_thread_id_var
from agent.modules.workflows.run_config import make_context

@pytest.fixture
def mock_session_manager():
    with patch("agent.modules.tools.builtin.shell.session_tools.session_manager") as mock:
        mock.sessions = {"session-A": MagicMock(), "session-B": MagicMock(), "session-C": MagicMock()}
        # close_session returns True if session exists, False otherwise
        def side_effect(sid, scope_id=None):
            if sid in mock.sessions:
                del mock.sessions[sid]
                return True
            return False
        mock.close_session.side_effect = side_effect
        yield mock

def test_bash_close_none(mock_session_manager):
    # Pass None should close all sessions
    res = bash_close.invoke({"session_ids": None})
    mock_session_manager.close_all_sessions.assert_called_once_with(scope_id=None)
    assert "Closed all 3 session(s)." in res

def test_bash_close_single_string(mock_session_manager):
    res = bash_close.invoke({"session_ids": "session-A"})
    mock_session_manager.close_session.assert_called_with("session-A", scope_id=None)
    assert "Closed 1 session(s): session-A" in res
    assert "session-A" not in mock_session_manager.sessions

def test_bash_close_json_array_string(mock_session_manager):
    # Pass JSON-encoded array as string (what the agent did and failed before)
    res = bash_close.invoke({"session_ids": '["session-A", "session-B"]'})
    assert mock_session_manager.close_session.call_count == 2
    mock_session_manager.close_session.assert_any_call("session-A", scope_id=None)
    mock_session_manager.close_session.assert_any_call("session-B", scope_id=None)
    assert "Closed 2 session(s): session-A, session-B" in res

def test_bash_close_comma_separated_string(mock_session_manager):
    res = bash_close.invoke({"session_ids": "session-A, session-C"})
    assert mock_session_manager.close_session.call_count == 2
    mock_session_manager.close_session.assert_any_call("session-A", scope_id=None)
    mock_session_manager.close_session.assert_any_call("session-C", scope_id=None)
    assert "Closed 2 session(s): session-A, session-C" in res

def test_bash_close_actual_list(mock_session_manager):
    res = bash_close.invoke({"session_ids": ["session-A", "session-B"]})
    assert mock_session_manager.close_session.call_count == 2
    assert "Closed 2 session(s): session-A, session-B" in res

def test_bash_close_not_found(mock_session_manager):
    res = bash_close.invoke({"session_ids": "session-Z"})
    assert "Session IDs not found: session-Z" in res

def test_bash_close_empty_string(mock_session_manager):
    # Empty string should behave like None (close all)
    res = bash_close.invoke({"session_ids": ""})
    mock_session_manager.close_all_sessions.assert_called_once_with(scope_id=None)
    assert "Closed all 3 session(s)." in res


def test_bash_close_none_only_closes_current_thread(mock_session_manager):
    token = current_thread_id_var.set("thread-1")
    try:
        mock_session_manager.list_sessions.return_value = [
            {"session_id": "session-A"},
            {"session_id": "session-B"},
        ]

        res = bash_close.invoke({"session_ids": None})
    finally:
        current_thread_id_var.reset(token)

    mock_session_manager.list_sessions.assert_called_once_with(scope_id="thread-1")
    mock_session_manager.close_all_sessions.assert_called_once_with(scope_id="thread-1")
    assert "Closed all 2 session(s)." in res


def test_bash_schema_describes_persistent_session_state():
    schema = bash.tool_call_schema.model_json_schema()
    properties = schema["properties"]

    assert "persistent shell process" in properties["command"]["description"]
    assert "cd subdir" in properties["command"]["description"]
    assert "same session_id share one shell process" in properties["session_id"]["description"]
    assert "current directory changes" in properties["session_id"]["description"]
    assert "runtime" not in properties


@pytest.mark.asyncio
async def test_bash_tool_node_injects_runtime_with_class_schema(tmp_path, monkeypatch):
    captured = {}

    def fake_execute_command(**kwargs):
        captured.update(kwargs)
        return {"status": "completed", "output": "ok", "stderr": ""}

    monkeypatch.setattr(
        "agent.modules.tools.builtin.shell.session_tools.session_manager.execute_command",
        fake_execute_command,
    )

    def request_command(_state):
        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "bash",
                            "args": {"command": "pwd", "timeout": 2},
                            "id": "call-1",
                        }
                    ],
                )
            ]
        }

    graph = StateGraph(MessagesState)
    graph.add_node("request", request_command)
    graph.add_node("tools", ToolNode([bash]))
    graph.add_edge(START, "request")
    graph.add_edge("request", "tools")
    graph.add_edge("tools", END)

    result = await graph.compile().ainvoke(
        {"messages": []},
        config={"configurable": {"thread_id": "thread-1"}},
        context=make_context(working_dir=str(tmp_path)),
    )

    assert result["messages"][-1].content == "STDOUT:\nok"
    assert captured["command"] == "pwd"
    assert captured["timeout"] == 2
    assert captured["scope_id"] == "thread-1"
    assert captured["working_dir"] == str(tmp_path)


@pytest.mark.asyncio
async def test_bash_routes_daytona_workspace_to_daytona_manager(monkeypatch):
    import agent.modules.tools.builtin.shell.session_tools as session_tools_module

    captured = {}

    class FakeDaytonaSessionManager:
        sessions = {}

        def execute_command(self, **kwargs):
            captured.update(kwargs)
            return {"status": "completed", "output": "remote", "stderr": ""}

        def has_session(self, session_id, scope_id=None):
            return False

    monkeypatch.setattr(
        session_tools_module.session_manager,
        "execute_command",
        lambda **kwargs: pytest.fail("local session manager should not be used"),
    )
    monkeypatch.setattr(
        session_tools_module,
        "daytona_session_manager",
        FakeDaytonaSessionManager(),
    )
    runtime = SimpleNamespace(
        context={
            "workspace": {
                "backend": "daytona",
                "locator": "sandbox-1",
                "label": "sandbox",
                "metadata": {"root": "workspace"},
            },
        },
        config={"configurable": {"thread_id": "thread-1"}},
    )

    result = await bash.coroutine(command="pwd", runtime=runtime, timeout=2)

    assert result == "STDOUT:\nremote"
    assert captured["command"] == "pwd"
    assert captured["workspace"].backend == "daytona"
    assert captured["workspace"].locator == "sandbox-1"
    assert captured["scope_id"] == "thread-1"


@pytest.mark.asyncio
async def test_bash_routes_modal_workspace_to_modal_manager(monkeypatch):
    import agent.modules.tools.builtin.shell.session_tools as session_tools_module

    captured = {}

    class FakeModalSessionManager:
        sessions = {}

        async def execute_command(self, **kwargs):
            captured.update(kwargs)
            return {"status": "completed", "output": "remote", "stderr": ""}

        def has_session(self, session_id, scope_id=None):
            return False

    monkeypatch.setattr(
        session_tools_module.session_manager,
        "execute_command",
        lambda **kwargs: pytest.fail("local session manager should not be used"),
    )
    monkeypatch.setattr(
        session_tools_module,
        "modal_session_manager",
        FakeModalSessionManager(),
    )
    runtime = SimpleNamespace(
        context={
            "workspace": {
                "backend": "modal",
                "locator": "sb-1",
                "label": "sandbox",
                "metadata": {"root": "/workspace"},
            },
        },
        config={"configurable": {"thread_id": "thread-1"}},
    )

    result = await bash.coroutine(command="pwd", runtime=runtime, timeout=2)

    assert result == "STDOUT:\nremote"
    assert captured["command"] == "pwd"
    assert captured["workspace"].backend == "modal"
    assert captured["workspace"].locator == "sb-1"
    assert captured["scope_id"] == "thread-1"


def test_daytona_session_manager_passes_root_thread_id_to_backend(monkeypatch):
    import agent.modules.tools.builtin.shell.daytona_session_manager as module

    captured_thread_ids: list[str | None] = []

    class FakePty:
        def __iter__(self):
            return iter(())

    class FakeProcess:
        def create_pty_session(self, *args, **kwargs):
            return FakePty()

    class FakeDaytonaBackend:
        root = "/workspace"
        process = FakeProcess()

        def __init__(self, workspace, thread_id=None):
            captured_thread_ids.append(thread_id)

    monkeypatch.setattr(module, "_get_daytona_backend_type", lambda: FakeDaytonaBackend)

    manager = module.DaytonaTerminalSessionManager()
    workspace = SimpleNamespace(
        backend="daytona",
        locator="sandbox-1",
        label="sandbox",
        metadata={"root": "workspace"},
    )

    manager.create_session(
        workspace=workspace,
        session_name="default",
        scope_id="thread-1:sub:worker:abc",
    )

    assert captured_thread_ids == ["thread-1"]


class _FakeStdin:
    def __init__(self, output_queue):
        self.output_queue = output_queue

    def write(self, text):
        marker = "echo ____CMD_DONE_"
        if marker not in text:
            return
        sentinel = "____CMD_DONE_" + text.split(marker, 1)[1].splitlines()[0]
        self.output_queue.put(sentinel)

    def flush(self):
        return None

    def close(self):
        return None


class _FakeProcess:
    pid = 999999

    def __init__(self, output_queue):
        self.stdin = _FakeStdin(output_queue)

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        return None

    def kill(self):
        return None


def _install_fake_session_factory(monkeypatch, manager):
    def fake_create_session(working_dir, session_name=None, scope_id=None):
        session_id = session_name or "generated"
        output_queue = Queue()
        error_queue = Queue()
        session = TerminalSession(
            session_id=session_id,
            scope_id=manager._normalize_scope_id(scope_id),
            working_dir=working_dir,
            process=_FakeProcess(output_queue),
            output_queue=output_queue,
            error_queue=error_queue,
        )
        manager.sessions[manager._session_key(session_id, scope_id)] = session
        return session_id

    monkeypatch.setattr(manager, "create_session", fake_create_session)


def test_terminal_session_manager_scopes_same_session_id_by_thread(tmp_path, monkeypatch):
    manager = TerminalSessionManager()
    _install_fake_session_factory(monkeypatch, manager)

    manager.execute_command(
        session_id="default",
        command="echo one",
        working_dir=str(tmp_path),
        scope_id="thread-1",
        timeout=1,
    )
    manager.execute_command(
        session_id="default",
        command="echo two",
        working_dir=str(tmp_path),
        scope_id="thread-2",
        timeout=1,
    )

    assert len(manager.sessions) == 2
    assert {session.scope_id for session in manager.sessions.values()} == {
        "thread-1",
        "thread-2",
    }


def test_terminal_session_manager_reuses_session_within_thread(tmp_path, monkeypatch):
    manager = TerminalSessionManager()
    _install_fake_session_factory(monkeypatch, manager)

    manager.execute_command(
        session_id="default",
        command="echo one",
        working_dir=str(tmp_path),
        scope_id="thread-1",
        timeout=1,
    )
    manager.execute_command(
        session_id="default",
        command="echo two",
        working_dir=str(tmp_path),
        scope_id="thread-1",
        timeout=1,
    )

    assert len(manager.sessions) == 1
    session = next(iter(manager.sessions.values()))
    assert session.session_id == "default"
    assert session.scope_id == "thread-1"


def test_terminal_session_manager_closes_thread_tree_sessions(tmp_path, monkeypatch):
    manager = TerminalSessionManager()
    _install_fake_session_factory(monkeypatch, manager)

    for scope_id in ["thread-1", "thread-1:sub:worker:abc", "thread-2"]:
        manager.execute_command(
            session_id="default",
            command="echo ok",
            working_dir=str(tmp_path),
            scope_id=scope_id,
            timeout=1,
        )

    assert manager.close_thread_sessions("thread-1") == 2
    assert len(manager.sessions) == 1
    remaining = next(iter(manager.sessions.values()))
    assert remaining.scope_id == "thread-2"
