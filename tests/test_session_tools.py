import pytest
from unittest.mock import MagicMock, patch
from agent.modules.tools.langchain.shell_tools.session_tools import bash_close

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
