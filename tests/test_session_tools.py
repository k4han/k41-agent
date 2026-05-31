import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from unittest.mock import MagicMock, patch
from agent.modules.tools.langchain.shell_tools.session_tools import bash, bash_close
from agent.modules.workflows.run_config import make_context

@pytest.fixture
def mock_session_manager():
    with patch("agent.modules.tools.langchain.shell_tools.session_tools.session_manager") as mock:
        mock.sessions = {"session-A": MagicMock(), "session-B": MagicMock(), "session-C": MagicMock()}
        # close_session returns True if session exists, False otherwise
        def side_effect(sid):
            if sid in mock.sessions:
                del mock.sessions[sid]
                return True
            return False
        mock.close_session.side_effect = side_effect
        yield mock

def test_bash_close_none(mock_session_manager):
    # Pass None should close all sessions
    res = bash_close.invoke({"session_ids": None})
    mock_session_manager.close_all_sessions.assert_called_once()
    assert "Closed all 3 session(s)." in res

def test_bash_close_single_string(mock_session_manager):
    res = bash_close.invoke({"session_ids": "session-A"})
    mock_session_manager.close_session.assert_called_with("session-A")
    assert "Closed 1 session(s): session-A" in res
    assert "session-A" not in mock_session_manager.sessions

def test_bash_close_json_array_string(mock_session_manager):
    # Pass JSON-encoded array as string (what the agent did and failed before)
    res = bash_close.invoke({"session_ids": '["session-A", "session-B"]'})
    assert mock_session_manager.close_session.call_count == 2
    mock_session_manager.close_session.assert_any_call("session-A")
    mock_session_manager.close_session.assert_any_call("session-B")
    assert "Closed 2 session(s): session-A, session-B" in res

def test_bash_close_comma_separated_string(mock_session_manager):
    res = bash_close.invoke({"session_ids": "session-A, session-C"})
    assert mock_session_manager.close_session.call_count == 2
    mock_session_manager.close_session.assert_any_call("session-A")
    mock_session_manager.close_session.assert_any_call("session-C")
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
    mock_session_manager.close_all_sessions.assert_called_once()
    assert "Closed all 3 session(s)." in res


def test_bash_schema_describes_persistent_session_state():
    schema = bash.tool_call_schema.model_json_schema()
    properties = schema["properties"]

    assert "persistent shell process" in properties["command"]["description"]
    assert "cd subdir" in properties["command"]["description"]
    assert "same session_id share one shell process" in properties["session_id"]["description"]
    assert "current directory changes" in properties["session_id"]["description"]
    assert "runtime" not in properties


def test_bash_tool_node_injects_runtime_with_class_schema(tmp_path, monkeypatch):
    captured = {}

    def fake_execute_command(**kwargs):
        captured.update(kwargs)
        return {"status": "completed", "output": "ok", "stderr": ""}

    monkeypatch.setattr(
        "agent.modules.tools.langchain.shell_tools.session_tools.session_manager.execute_command",
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

    result = graph.compile().invoke(
        {"messages": []},
        context=make_context(working_dir=str(tmp_path)),
    )

    assert result["messages"][-1].content == "STDOUT:\nok"
    assert captured["command"] == "pwd"
    assert captured["timeout"] == 2
    assert captured["working_dir"] == str(tmp_path)
