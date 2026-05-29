"""Session-based shell tools for executing commands and managing persistent terminal sessions."""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.tools import tool, InjectedToolArg
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory
from agent.modules.tools.langchain.working_dir import get_working_dir
from agent.modules.tools.result import ToolError, ToolErrorCode
from agent.modules.tools.langchain.shell_tools.session_manager import session_manager


@register_tool(
    category=ToolCategory.SHELL,
    capabilities=[
        ToolCapability.EXEC_SHELL,
        ToolCapability.REQUIRES_WORKSPACE,
    ],
    tags=["shell"],
)
@tool
def bash(
    command: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
    session_id: str = "default",
    timeout: float = 30.0,
    run_in_background: bool = False,
    force: bool = False,
) -> str:
    """Run a shell command in a persistent terminal session.

    A session is auto-created if it doesn't exist. This tool supports maintaining state
    across multiple calls (e.g. current directory changes, environment variables, background processes).

    Args:
        command: Command to execute in the terminal session.
        session_id: ID of the terminal session. Defaults to 'default'.
        timeout: Maximum wait time in seconds for the command output. Defaults to 30.
        run_in_background: Set to True to run processes in the background without waiting for output.
        force: Set to True to force execution even when a background process is running in the session.
    """
    try:
        working_dir = get_working_dir(runtime)
        res = session_manager.execute_command(
            session_id=session_id,
            command=command,
            timeout=timeout,
            run_in_background=run_in_background,
            working_dir=working_dir,
            force=force,
        )

        if "error" in res:
            raise ToolError(ToolErrorCode.EXECUTION_ERROR, res["error"])
        if "warning" in res:
            return f"Warning: {res['warning']}"

        status = res.get("status")
        if status == "running_background":
            return f"Command started in background on session '{session_id}'."

        output = res.get("output", "")
        stderr = res.get("stderr", "")
        
        result_str = f"STDOUT:\n{output}"
        if stderr:
            result_str += f"\nSTDERR:\n{stderr}"
        return result_str
    except ValueError as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc


@register_tool(
    category=ToolCategory.SHELL,
    capabilities=[
        ToolCapability.EXEC_SHELL,
        ToolCapability.REQUIRES_WORKSPACE,
    ],
    tags=["shell"],
)
@tool
def bash_read_output(
    session_id: str,
    timeout: float = 1.0,
) -> str:
    """Get stdout and stderr output from a background process running in a terminal session.

    Args:
        session_id: ID of the active terminal session.
        timeout: Time in seconds to wait for new output.
    """
    res = session_manager.get_session_output(session_id, timeout)
    if "error" in res:
        raise ToolError(ToolErrorCode.NOT_FOUND, res["error"])

    output = res.get("output", "")
    stderr = res.get("stderr", "")
    running_status = "Running" if res.get("is_running") else "Stopped"
    
    result_str = f"Status: {running_status}\nSTDOUT:\n{output}"
    if stderr:
        result_str += f"\nSTDERR:\n{stderr}"
    return result_str


@register_tool(
    category=ToolCategory.SHELL,
    capabilities=[
        ToolCapability.EXEC_SHELL,
    ],
    tags=["shell"],
)
@tool
def bash_send_input(
    session_id: str,
    text: str,
) -> str:
    """Send text input to stdin of a running process in a terminal session.

    Use this when a running program is waiting for user input (e.g., confirmation prompts,
    interactive menus, password prompts, or any 'Press Enter to continue' scenarios).

    Args:
        session_id: ID of the terminal session.
        text: The text to send as stdin input. A newline is automatically appended.
    """
    res = session_manager.send_input(session_id, text)
    if "error" in res:
        raise ToolError(ToolErrorCode.EXECUTION_ERROR, res["error"])
    return f"Sent input to session '{session_id}': {text!r}"


@register_tool(
    category=ToolCategory.SHELL,
    capabilities=[
        ToolCapability.EXEC_SHELL,
    ],
    tags=["shell"],
)
@tool
def bash_interrupt(
    session_id: str,
    signal_type: str = "interrupt",
) -> str:
    """Send a signal to interrupt or terminate the running process in a terminal session.

    Use this to cancel a long-running command (like Ctrl+C) or to force-terminate a stuck process.

    Args:
        session_id: ID of the terminal session.
        signal_type: Type of signal to send. 'interrupt' sends Ctrl+C (SIGINT),
            'terminate' sends SIGTERM. Defaults to 'interrupt'.
    """
    res = session_manager.send_signal(session_id, signal_type)
    if "error" in res:
        raise ToolError(ToolErrorCode.EXECUTION_ERROR, res["error"])
    return (
        f"Sent {signal_type} signal to session '{session_id}' "
        f"(target PID: {res.get('target_pid', 'unknown')})."
    )


@register_tool(
    category=ToolCategory.SHELL,
    capabilities=[
        ToolCapability.EXEC_SHELL,
    ],
    tags=["shell"],
)
@tool
def bash_list_sessions() -> str:
    """List all active interactive terminal sessions."""
    sessions = session_manager.list_sessions()
    if not sessions:
        return "No active terminal sessions."

    result = "Active terminal sessions:\n"
    for s in sessions:
        status = "✓ Running" if s["is_running"] else "✗ Stopped"
        result += f"  • {s['session_id']} - {s['working_dir']} [{status}]\n"
    return result


@register_tool(
    category=ToolCategory.SHELL,
    capabilities=[
        ToolCapability.EXEC_SHELL,
    ],
    tags=["shell"],
)
@tool
def bash_close(
    session_ids: str | list[str] | None = None,
) -> str:
    """Close one or more active terminal sessions, terminating all child processes.

    Args:
        session_ids: A single session ID or a list of session IDs to close.
            Pass None to close all active sessions.
    """
    import json

    if isinstance(session_ids, str):
        stripped = session_ids.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    ids = [str(x).strip() for x in parsed]
                else:
                    ids = [stripped]
            except Exception:
                ids = [session_ids]
        elif ',' in stripped:
            ids = [x.strip() for x in stripped.split(',') if x.strip()]
        else:
            ids = [stripped] if stripped else []
    else:
        ids = session_ids or []
    if not ids:
        count = len(session_manager.sessions)
        session_manager.close_all_sessions()
        return f"Closed all {count} session(s)."

    closed = []
    not_found = []
    for sid in ids:
        if session_manager.close_session(sid):
            closed.append(sid)
        else:
            not_found.append(sid)

    result = []
    if closed:
        result.append(f"Closed {len(closed)} session(s): {', '.join(closed)}")
    if not_found:
        result.append(f"Session IDs not found: {', '.join(not_found)}")
    return "\n".join(result) if result else "No sessions were closed."
