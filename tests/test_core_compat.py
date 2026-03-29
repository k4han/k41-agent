from agent.core import run_agent, run_agent_full
from agent.core.session import SessionManager
from agent.modules.agent_runtime.public import run_agent as runtime_run_agent
from agent.modules.agent_runtime.public import run_agent_full as runtime_run_agent_full
from agent.modules.agent_runtime.application.session import SessionManager as RuntimeSessionManager


def test_core_runner_shims_delegate_to_agent_runtime():
    assert run_agent is runtime_run_agent
    assert run_agent_full is runtime_run_agent_full


def test_core_session_shim_delegates_to_agent_runtime():
    assert SessionManager is RuntimeSessionManager
