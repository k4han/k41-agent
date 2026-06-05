"""Session-based shell tools for executing commands and managing persistent terminal sessions."""

from typing import Any

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory
from agent.modules.tools.builtin.workspace import get_workspace
from agent.modules.tools.runtime.context import ToolContext
from agent.modules.tools.result import ToolError, ToolErrorCode
from agent.modules.tools.builtin.shell.daytona_session_manager import daytona_session_manager
from agent.modules.tools.builtin.shell.modal_session_manager import modal_session_manager
from agent.modules.tools.builtin.shell.session_manager import session_manager
from agent.modules.agent_runtime import current_thread_id_var


def _scope_id_from_runtime(runtime: ToolRuntime[Any, Any]) -> str | None:
    context = ToolContext.from_runtime(runtime)
    return context.thread_id or current_thread_id_var.get()


def _current_scope_id() -> str | None:
    return current_thread_id_var.get()


class BashInput(BaseModel):
    """Input schema for executing commands in persistent shell sessions."""

    command: str = Field(
        description=(
            "Command to execute inside the persistent shell process for session_id. "
            "Shell state is preserved across calls to the same session_id within the "
            "same conversation thread: if an earlier command ran 'cd subdir', the next "
            "command in that same thread/session starts there. Use a different "
            "session_id when you need another isolated shell inside the same thread."
        ),
    )
    session_id: str = Field(
        default="default",
        description=(
            "Persistent terminal session identifier. Calls with the same session_id share "
            "one shell process within the current conversation thread and therefore keep "
            "current directory changes, environment variables, shell state, and background "
            "processes. Defaults to 'default'."
        ),
    )
    timeout: float = Field(
        default=30.0,
        description="Maximum wait time in seconds for command output.",
    )
    run_in_background: bool = Field(
        default=False,
        description=(
            "Run the command without waiting for completion. Read later output from the "
            "same session_id with bash_read_output."
        ),
    )
    force: bool = Field(
        default=False,
        description=(
            "Force execution even when the same session already has a background process running."
        ),
    )


class BashReadOutputInput(BaseModel):
    """Input schema for reading background output from a shell session."""

    session_id: str = Field(
        description=(
            "Persistent terminal session identifier to read from. Use the same session_id "
            "that started the background command."
        ),
    )
    timeout: float = Field(
        default=1.0,
        description="Time in seconds to wait for new output from the session.",
    )


class BashSendInputInput(BaseModel):
    """Input schema for sending stdin to a shell session."""

    session_id: str = Field(
        description=(
            "Persistent terminal session identifier. Input is sent to the process running "
            "inside this existing session."
        ),
    )
    text: str = Field(
        description="Text to send as stdin input. A newline is automatically appended.",
    )


class BashInterruptInput(BaseModel):
    """Input schema for interrupting a shell session."""

    session_id: str = Field(
        description="Persistent terminal session identifier to interrupt or terminate.",
    )
    signal_type: str = Field(
        default="interrupt",
        description=(
            "Signal type to send. Use 'interrupt' for Ctrl+C/SIGINT or 'terminate' for SIGTERM."
        ),
    )


class BashCloseInput(BaseModel):
    """Input schema for closing persistent shell sessions."""

    session_ids: str | list[str] | None = Field(
        default=None,
        description=(
            "Session ID, list of session IDs, JSON array string, comma-separated IDs, "
            "or None to close all persistent terminal sessions."
        ),
    )


@register_tool(
    category=ToolCategory.SHELL,
    capabilities=[
        ToolCapability.EXEC_SHELL,
        ToolCapability.REQUIRES_WORKSPACE,
    ],
    tags=["shell"],
)
@tool(args_schema=BashInput)
async def bash(
    command: str,
    runtime: ToolRuntime[Any, Any],
    session_id: str = "default",
    timeout: float = 30.0,
    run_in_background: bool = False,
    force: bool = False,
) -> str:
    """Run a shell command in a persistent terminal session.

    A session is auto-created if it doesn't exist. Each ``session_id`` maps to one
    long-lived shell process, so calls to the same session preserve shell state
    across turns. For example, after running ``cd src`` in session ``default``,
    the next ``bash`` call with ``session_id="default"`` starts in ``src``.

    Args:
        command: Command to execute in the terminal session.
        session_id: ID of the terminal session. Calls with the same ID share current
            directory, environment variables, shell state, and background processes.
        timeout: Maximum wait time in seconds for the command output. Defaults to 30.
        run_in_background: Set to True to run processes in the background without waiting for output.
        force: Set to True to force execution even when a background process is running in the session.
    """
    try:
        workspace = get_workspace(runtime)
        scope_id = _scope_id_from_runtime(runtime)
        if workspace.backend == "daytona":
            res = daytona_session_manager.execute_command(
                session_id=session_id,
                command=command,
                timeout=timeout,
                run_in_background=run_in_background,
                workspace=workspace,
                force=force,
                scope_id=scope_id,
            )
        elif workspace.backend == "modal":
            res = await modal_session_manager.execute_command(
                session_id=session_id,
                command=command,
                timeout=timeout,
                run_in_background=run_in_background,
                workspace=workspace,
                force=force,
                scope_id=scope_id,
            )
        else:
            res = session_manager.execute_command(
                session_id=session_id,
                command=command,
                timeout=timeout,
                run_in_background=run_in_background,
                working_dir=workspace.locator,
                force=force,
                scope_id=scope_id,
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
@tool(args_schema=BashReadOutputInput)
def bash_read_output(
    session_id: str,
    timeout: float = 1.0,
) -> str:
    """Get stdout and stderr output from a background process running in a terminal session.

    Args:
        session_id: ID of the active terminal session.
        timeout: Time in seconds to wait for new output.
    """
    scope_id = _current_scope_id()
    if daytona_session_manager.has_session(session_id, scope_id=scope_id):
        res = daytona_session_manager.get_session_output(
            session_id,
            timeout,
            scope_id=scope_id,
        )
    elif modal_session_manager.has_session(session_id, scope_id=scope_id):
        res = modal_session_manager.get_session_output(
            session_id,
            timeout,
            scope_id=scope_id,
        )
    else:
        res = session_manager.get_session_output(session_id, timeout, scope_id=scope_id)
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
@tool(args_schema=BashSendInputInput)
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
    scope_id = _current_scope_id()
    if daytona_session_manager.has_session(session_id, scope_id=scope_id):
        res = daytona_session_manager.send_input(session_id, text, scope_id=scope_id)
    elif modal_session_manager.has_session(session_id, scope_id=scope_id):
        res = modal_session_manager.send_input(session_id, text, scope_id=scope_id)
    else:
        res = session_manager.send_input(session_id, text, scope_id=scope_id)
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
@tool(args_schema=BashInterruptInput)
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
    scope_id = _current_scope_id()
    if daytona_session_manager.has_session(session_id, scope_id=scope_id):
        res = daytona_session_manager.send_signal(
            session_id,
            signal_type,
            scope_id=scope_id,
        )
    elif modal_session_manager.has_session(session_id, scope_id=scope_id):
        res = modal_session_manager.send_signal(
            session_id,
            signal_type,
            scope_id=scope_id,
        )
    else:
        res = session_manager.send_signal(session_id, signal_type, scope_id=scope_id)
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
    scope_id = _current_scope_id()
    sessions = session_manager.list_sessions(scope_id=scope_id)
    sessions.extend(daytona_session_manager.list_sessions(scope_id=scope_id))
    sessions.extend(modal_session_manager.list_sessions(scope_id=scope_id))
    if not sessions:
        return "No active terminal sessions."

    result = "Active terminal sessions:\n"
    for s in sessions:
        status = "✓ Running" if s["is_running"] else "✗ Stopped"
        scope = s.get("scope_id")
        scope_display = f" ({scope})" if scope_id is None and scope else ""
        result += f"  • {s['session_id']}{scope_display} - {s['working_dir']} [{status}]\n"
    return result


@register_tool(
    category=ToolCategory.SHELL,
    capabilities=[
        ToolCapability.EXEC_SHELL,
    ],
    tags=["shell"],
)
@tool(args_schema=BashCloseInput)
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
    scope_id = _current_scope_id()
    if not ids:
        if scope_id is None:
            count = (
                len(session_manager.sessions)
                + len(daytona_session_manager.sessions)
                + len(modal_session_manager.sessions)
            )
        else:
            count = len(session_manager.list_sessions(scope_id=scope_id))
            count += len(daytona_session_manager.list_sessions(scope_id=scope_id))
            count += len(modal_session_manager.list_sessions(scope_id=scope_id))
        session_manager.close_all_sessions(scope_id=scope_id)
        daytona_session_manager.close_all_sessions(scope_id=scope_id)
        modal_session_manager.close_all_sessions(scope_id=scope_id)
        return f"Closed all {count} session(s)."

    closed = []
    not_found = []
    for sid in ids:
        local_closed = session_manager.close_session(sid, scope_id=scope_id)
        daytona_closed = daytona_session_manager.close_session(sid, scope_id=scope_id)
        modal_closed = modal_session_manager.close_session(sid, scope_id=scope_id)
        if local_closed or daytona_closed or modal_closed:
            closed.append(sid)
        else:
            not_found.append(sid)

    result = []
    if closed:
        result.append(f"Closed {len(closed)} session(s): {', '.join(closed)}")
    if not_found:
        result.append(f"Session IDs not found: {', '.join(not_found)}")
    return "\n".join(result) if result else "No sessions were closed."
