"""Agent tools for Instabox machines, preview ports, snapshots and the desktop.

Every operation is its own tool so agents can be granted each one separately.
Desktop tools run one per turn and refuse input while a person controls the desktop.
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Annotated, Any, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolArg, StructuredTool
from langgraph.prebuilt import ToolRuntime
from pydantic import Field

from agent.modules.instabox import (
    InstaboxApiError,
    InstaboxConfirmationError,
    InstaboxConflictError,
    InstaboxDisabledError,
    get_instabox_service,
    redact,
)
from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory
from agent.modules.tools.result import ToolError, ToolErrorCode

Runtime = Annotated[ToolRuntime[Any, Any], InjectedToolArg]
MachineId = Annotated[
    str | None,
    Field(description="Instabox machine ID (m_...) or name. Omit to use the machine of the current Instabox workspace."),
]
INSTABOX_TOOLS: dict[str, BaseTool] = {}


def _authorize(runtime, name: str, resource: str, values: dict[str, Any]) -> None:
    from agent.modules.tools.coding.adapter import invocation_context
    from agent.modules.tools.coding.models import CodingError
    from agent.modules.tools.coding.service import get_coding_service

    try:
        get_coding_service().permissions.assert_allowed(invocation_context(runtime), name, resource, arguments=values)
    except CodingError as exc:
        raise ToolError(ToolErrorCode.PERMISSION_DENIED, str(exc)) from None


def _machine(runtime, machine_id: str | None) -> str:
    if machine_id and machine_id.strip():
        return machine_id.strip()
    from agent.modules.tools.builtin.workspace import get_workspace

    workspace = get_workspace(runtime)
    if workspace.backend != "instabox":
        raise ToolError(
            ToolErrorCode.INVALID_INPUT,
            "Pass machine_id: the current workspace does not run on an Instabox machine.",
        )
    return workspace.locator


def _owner(runtime) -> str:
    from agent.modules.tools.runtime.context import get_context_value, get_thread_id

    return str(get_context_value(runtime.context, "browser_owner_id", None) or get_thread_id(runtime.config) or "agent")


async def _call(coro):
    try:
        return await coro
    except ToolError:
        raise
    except InstaboxDisabledError as exc:
        raise ToolError(ToolErrorCode.EXECUTION_ERROR, f"instabox_disabled: {exc}") from None
    except InstaboxConfirmationError as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from None
    except InstaboxConflictError as exc:
        raise ToolError(ToolErrorCode.EXECUTION_ERROR, f"{exc.code}: {exc}") from None
    except InstaboxApiError as exc:
        code = ToolErrorCode.NOT_FOUND if exc.is_not_found else (
            ToolErrorCode.PERMISSION_DENIED if exc.is_auth_error else ToolErrorCode.UPSTREAM
        )
        hint = f" Retry after {exc.retry_after:.0f}s." if exc.retry_after else ""
        raise ToolError(code, f"{exc.code or exc.status}: {redact(str(exc))}.{hint}") from None
    except (ValueError, LookupError) as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from None
    except TimeoutError as exc:
        raise ToolError(ToolErrorCode.TIMEOUT, str(exc) or "Instabox did not answer in time.") from None


def _json(value: Any) -> str:
    return json.dumps(redact(value), ensure_ascii=False, default=str)


def registered(name: str, coroutine, description: str, *, mutates: bool = False, desktop: bool = False) -> BaseTool:
    capabilities = [ToolCapability.NETWORK, ToolCapability.ASYNC_ONLY]
    if mutates:
        capabilities.append(ToolCapability.MUTATES_STATE)
    if desktop:
        capabilities += [ToolCapability.REQUIRES_THREAD]
    tool = register_tool(category=ToolCategory.SANDBOX, capabilities=capabilities, tags=["instabox"])(
        StructuredTool.from_function(name=name, coroutine=coroutine, description=description)
    )
    INSTABOX_TOOLS[name] = tool
    return tool


# Machines ---------------------------------------------------------------- #


async def _machine_list(runtime: Runtime):
    _authorize(runtime, "instabox_machine_list", "*", {})
    return _json(await _call(get_instabox_service().list_machines()))


async def _machine_get(runtime: Runtime, machine_id: MachineId = None):
    target = _machine(runtime, machine_id)
    _authorize(runtime, "instabox_machine_get", target, {})
    return _json(await _call(get_instabox_service().get_machine(target)))


async def _machine_create(
    runtime: Runtime,
    name: Annotated[str | None, Field(description="Optional machine name: lowercase letters, digits and '-'.")] = None,
    template: Annotated[str | None, Field(description="Template ID. Defaults to the configured template.")] = None,
    snapshot_id: Annotated[str | None, Field(description="Start from a ready snapshot instead of a template.")] = None,
):
    values = {"name": name, "template": template, "snapshot_id": snapshot_id}
    _authorize(runtime, "instabox_machine_create", name or "*", values)
    service = get_instabox_service()
    if snapshot_id:
        record = await _call(service.create_machine_from_snapshot(snapshot_id, name=name, created_by=_owner(runtime), wait=True))
    else:
        record = await _call(service.create_machine(
            name=name, template=template, metadata={"k41_created_by": "agent"}, created_by=_owner(runtime), wait=True,
        ))
    return _json(record)


def _action_tool(action: str):
    async def run(runtime: Runtime, machine_id: MachineId = None):
        target = _machine(runtime, machine_id)
        _authorize(runtime, f"instabox_machine_{action}", target, {})
        return _json(await _call(get_instabox_service().machine_action(
            target, action, created_by=_owner(runtime), wait=True,
        )))

    return run


async def _machine_fork(
    runtime: Runtime,
    machine_id: MachineId = None,
    name: Annotated[str | None, Field(description="Name of the new independent machine.")] = None,
):
    target = _machine(runtime, machine_id)
    _authorize(runtime, "instabox_machine_fork", target, {"name": name})
    record = await _call(get_instabox_service().fork_machine(target, name=name, created_by=_owner(runtime), wait=True))
    if record.get("status") == "failed" and "fork_unavailable" in json.dumps(record.get("error") or {}):
        record["hint"] = "Fork is unavailable on this host. Create a snapshot, then create a machine from it."
    return _json(record)


async def _machine_delete(
    runtime: Runtime,
    machine_id: Annotated[str, Field(description="Exact machine ID to delete.")],
    confirm: Annotated[str, Field(description="Repeat the exact machine ID to confirm deletion.")],
):
    _authorize(runtime, "instabox_machine_delete", machine_id, {"confirm": confirm})
    return _json(await _call(get_instabox_service().delete_machine(
        machine_id, confirm=confirm, created_by=_owner(runtime), wait=True,
    )))


# Ports ------------------------------------------------------------------- #


async def _port_list(runtime: Runtime, machine_id: MachineId = None):
    target = _machine(runtime, machine_id)
    _authorize(runtime, "instabox_port_list", target, {})
    return _json(await _call(get_instabox_service().list_ports(target)))


async def _port_publish(
    runtime: Runtime,
    port: Annotated[int, Field(ge=1024, le=65535, description="HTTP port of a server running in the machine.")],
    machine_id: MachineId = None,
    access: Annotated[Literal["private", "public"], Field(description="private (default) needs a token link; public is open to anyone.")] = "private",
    name: Annotated[str | None, Field(description="Optional display name.")] = None,
):
    target = _machine(runtime, machine_id)
    _authorize(runtime, "instabox_port_publish", f"{target}:{port}", {"access": access})
    return _json(await _call(get_instabox_service().publish_port(target, port, access=access, name=name)))


async def _port_set_access(
    runtime: Runtime,
    port: Annotated[int, Field(ge=1024, le=65535)],
    access: Literal["private", "public"],
    machine_id: MachineId = None,
):
    target = _machine(runtime, machine_id)
    _authorize(runtime, "instabox_port_set_access", f"{target}:{port}", {"access": access})
    return _json(await _call(get_instabox_service().set_port_access(target, port, access)))


async def _port_rotate_token(runtime: Runtime, port: Annotated[int, Field(ge=1024, le=65535)], machine_id: MachineId = None):
    target = _machine(runtime, machine_id)
    _authorize(runtime, "instabox_port_rotate_token", f"{target}:{port}", {})
    return _json(await _call(get_instabox_service().rotate_port_token(target, port)))


async def _port_close(runtime: Runtime, port: Annotated[int, Field(ge=1024, le=65535)], machine_id: MachineId = None):
    target = _machine(runtime, machine_id)
    _authorize(runtime, "instabox_port_close", f"{target}:{port}", {})
    await _call(get_instabox_service().close_port(target, port))
    return _json({"closed": True, "port": port})


# Snapshots --------------------------------------------------------------- #


async def _snapshot_list(runtime: Runtime):
    _authorize(runtime, "instabox_snapshot_list", "*", {})
    return _json(await _call(get_instabox_service().list_snapshots()))


async def _snapshot_get(runtime: Runtime, snapshot_id: str):
    _authorize(runtime, "instabox_snapshot_get", snapshot_id, {})
    return _json(await _call(get_instabox_service().get_snapshot(snapshot_id)))


async def _snapshot_create(
    runtime: Runtime,
    name: Annotated[str, Field(description="Unique snapshot name: lowercase letters, digits and '-'.")],
    machine_id: MachineId = None,
    description: str | None = None,
):
    target = _machine(runtime, machine_id)
    _authorize(runtime, "instabox_snapshot_create", target, {"name": name})
    return _json(await _call(get_instabox_service().create_snapshot(
        target, name=name, description=description, created_by=_owner(runtime), wait=True,
    )))


async def _snapshot_delete(
    runtime: Runtime,
    snapshot_id: str,
    confirm: Annotated[str, Field(description="Repeat the exact snapshot ID to confirm deletion.")],
):
    _authorize(runtime, "instabox_snapshot_delete", snapshot_id, {"confirm": confirm})
    await _call(get_instabox_service().delete_snapshot(snapshot_id, confirm=confirm))
    return _json({"deleted": True, "snapshot_id": snapshot_id})


async def _snapshot_restore(
    runtime: Runtime,
    machine_id: Annotated[str, Field(description="Exact machine ID to restore in place.")],
    snapshot_id: Annotated[str, Field(description="Exact ID of a ready snapshot.")],
):
    # Restore always asks the user through the application approval step.
    _authorize(runtime, "instabox_snapshot_restore", f"{machine_id}<-{snapshot_id}", {})
    return _json(await _call(get_instabox_service().restore_machine(
        machine_id, snapshot_id, confirm_machine=machine_id, confirm_snapshot=snapshot_id, created_by=_owner(runtime),
    )))


# Desktop ----------------------------------------------------------------- #


async def _desktop_start(runtime: Runtime, machine_id: MachineId = None):
    target = _machine(runtime, machine_id)
    _authorize(runtime, "instabox_desktop_start", target, {})
    return _json(await _call(get_instabox_service().desktop.ensure_started(target)))


async def _save_screenshot(runtime, data: bytes) -> str | None:
    from agent.modules.tools.builtin.workspace import get_workspace
    from agent.modules.tools.runtime.context import get_thread_id
    from agent.modules.workspaces import get_workspace_file_io

    path = f".k41-agent/instabox/screenshots/{uuid.uuid4().hex}.png"
    try:
        workspace = get_workspace(runtime)
        if workspace.backend == "local":
            from agent.modules.tools.runtime.thread_storage import ensure_physical_workspace_storage

            ensure_physical_workspace_storage(workspace.locator)
        file_io = await get_workspace_file_io(workspace, thread_id=get_thread_id(runtime.config))
        await file_io.write_bytes(path, data)
    except Exception:
        return None
    return path


async def _desktop_screenshot(runtime: Runtime, machine_id: MachineId = None):
    target = _machine(runtime, machine_id)
    _authorize(runtime, "instabox_desktop_screenshot", target, {})
    data = await _call(get_instabox_service().desktop.screenshot(target))
    path = await _save_screenshot(runtime, data)
    text = "Desktop screenshot captured." + (f"\nSaved to workspace: {path}" if path else "")
    return ToolMessage(
        content=[{"type": "text", "text": text},
                 {"type": "image", "base64": base64.b64encode(data).decode("ascii"), "mime_type": "image/png"}],
        name="instabox_desktop_screenshot",
        tool_call_id=runtime.tool_call_id,
        artifact={"display_content": text, "instabox": {"machine_id": target, "workspace_path": path}},
    )


def _desktop_action(action: str):
    async def run(runtime: Runtime, machine_id: MachineId = None, **values: Any):
        target = _machine(runtime, machine_id)
        _authorize(runtime, f"instabox_desktop_{action}", target, values)
        return _json(await _call(get_instabox_service().desktop.act(target, action, **values)))

    return run


async def _desktop_open_url(runtime: Runtime, url: str, machine_id: MachineId = None):
    return await _desktop_action("open_url")(runtime, machine_id, url=url)


async def _desktop_click(
    runtime: Runtime,
    x: Annotated[int, Field(ge=0, description="Pixels from the left edge.")],
    y: Annotated[int, Field(ge=0, description="Pixels from the top edge.")],
    button: Literal["left", "middle", "right"] = "left",
    double: bool = False,
    machine_id: MachineId = None,
):
    return await _desktop_action("click")(runtime, machine_id, x=x, y=y, button=button, double=double)


async def _desktop_move(runtime: Runtime, x: Annotated[int, Field(ge=0)], y: Annotated[int, Field(ge=0)], machine_id: MachineId = None):
    return await _desktop_action("move")(runtime, machine_id, x=x, y=y)


async def _desktop_drag(
    runtime: Runtime,
    x1: Annotated[int, Field(ge=0)],
    y1: Annotated[int, Field(ge=0)],
    x2: Annotated[int, Field(ge=0)],
    y2: Annotated[int, Field(ge=0)],
    machine_id: MachineId = None,
):
    return await _desktop_action("drag")(runtime, machine_id, x1=x1, y1=y1, x2=x2, y2=y2)


async def _desktop_scroll(
    runtime: Runtime,
    direction: Literal["up", "down", "left", "right"] = "down",
    amount: Annotated[int, Field(ge=1, le=50)] = 3,
    x: int | None = None,
    y: int | None = None,
    machine_id: MachineId = None,
):
    return await _desktop_action("scroll")(runtime, machine_id, direction=direction, amount=amount, x=x, y=y)


async def _desktop_type(runtime: Runtime, text: Annotated[str, Field(min_length=1, max_length=4000)], machine_id: MachineId = None):
    return await _desktop_action("type")(runtime, machine_id, text=text)


async def _desktop_press(
    runtime: Runtime,
    keys: Annotated[list[str], Field(min_length=1, description="Key or chord, e.g. ['Return'] or ['ctrl', 'l'] (xdotool key names).")],
    machine_id: MachineId = None,
):
    return await _desktop_action("press")(runtime, machine_id, keys=keys)


instabox_machine_list = registered("instabox_machine_list", _machine_list,
    "List Instabox machines with their normalized status. Listing never wakes a machine.")
instabox_machine_get = registered("instabox_machine_get", _machine_get,
    "Show one Instabox machine: state, size, template and labels. Never wakes the machine.")
instabox_machine_create = registered("instabox_machine_create", _machine_create,
    "Create an Instabox machine from the configured template or from a ready snapshot and wait until it runs. The machine appears in the list; attach it to a workspace from the dashboard.", mutates=True)
instabox_machine_start = registered("instabox_machine_start", _action_tool("start"),
    "Start a stopped Instabox machine and wait until it runs.", mutates=True)
instabox_machine_stop = registered("instabox_machine_stop", _action_tool("stop"),
    "Stop a running Instabox machine. Running programs end; files are kept.", mutates=True)
instabox_machine_suspend = registered("instabox_machine_suspend", _action_tool("suspend"),
    "Pause a running Instabox machine, keeping its running programs.", mutates=True)
instabox_machine_resume = registered("instabox_machine_resume", _action_tool("resume"),
    "Resume a paused Instabox machine where it left off.", mutates=True)
instabox_machine_fork = registered("instabox_machine_fork", _machine_fork,
    "Copy a running Instabox machine's disks into a new independent machine on the same host. Memory and running programs are not copied. If fork is unavailable, create a snapshot and start a machine from it.", mutates=True)
instabox_machine_delete = registered("instabox_machine_delete", _machine_delete,
    "Permanently delete an Instabox machine and its storage. Requires the exact machine ID twice and asks the user by default.", mutates=True)

instabox_port_list = registered("instabox_port_list", _port_list,
    "List the published HTTPS preview ports of an Instabox machine.")
instabox_port_publish = registered("instabox_port_publish", _port_publish,
    "Give an HTTP port of an Instabox machine an HTTPS preview URL. Private by default: the returned link carries a token. Use this instead of localhost to view apps running in the machine.", mutates=True)
instabox_port_set_access = registered("instabox_port_set_access", _port_set_access,
    "Switch a published preview port between private and public access.", mutates=True)
instabox_port_rotate_token = registered("instabox_port_rotate_token", _port_rotate_token,
    "Issue a new token for a private preview port and end connections using the old link.", mutates=True)
instabox_port_close = registered("instabox_port_close", _port_close,
    "Remove a port's preview URL and close its active connections.", mutates=True)

instabox_snapshot_list = registered("instabox_snapshot_list", _snapshot_list,
    "List Instabox snapshots and their state (pending, ready or failed).")
instabox_snapshot_get = registered("instabox_snapshot_get", _snapshot_get,
    "Show one Instabox snapshot.")
instabox_snapshot_create = registered("instabox_snapshot_create", _snapshot_create,
    "Save an Instabox machine's system files and storage as a snapshot and wait until it is ready.", mutates=True)
instabox_snapshot_delete = registered("instabox_snapshot_delete", _snapshot_delete,
    "Delete an Instabox snapshot. Machines created from it are not affected. Asks the user by default.", mutates=True)
instabox_snapshot_restore = registered("instabox_snapshot_restore", _snapshot_restore,
    "Put an Instabox machine's disks back to a ready snapshot in place. Everything written since the snapshot is lost. Always asks the user to confirm the exact machine and snapshot; refuses while tasks or processes run on the machine.", mutates=True)

instabox_desktop_start = registered("instabox_desktop_start", _desktop_start,
    "Start the Instabox machine's desktop at 1280x800, installing it first if the template lacks one.", mutates=True, desktop=True)
instabox_desktop_screenshot = registered("instabox_desktop_screenshot", _desktop_screenshot,
    "Capture the Instabox desktop as an image visible to the model and save it under .k41-agent/instabox/screenshots/ in the active workspace. Call desktop tools one per turn.", desktop=True)
instabox_desktop_open_url = registered("instabox_desktop_open_url", _desktop_open_url,
    "Open an http(s) URL in the Instabox desktop browser.", mutates=True, desktop=True)
instabox_desktop_click = registered("instabox_desktop_click", _desktop_click,
    "Click at pixel coordinates on the Instabox desktop. Take a screenshot first. Refused while a person controls the desktop.", mutates=True, desktop=True)
instabox_desktop_move = registered("instabox_desktop_move", _desktop_move,
    "Move the mouse pointer on the Instabox desktop.", mutates=True, desktop=True)
instabox_desktop_drag = registered("instabox_desktop_drag", _desktop_drag,
    "Drag with the left mouse button between two points on the Instabox desktop.", mutates=True, desktop=True)
instabox_desktop_scroll = registered("instabox_desktop_scroll", _desktop_scroll,
    "Scroll the Instabox desktop, optionally at a pointer position.", mutates=True, desktop=True)
instabox_desktop_type = registered("instabox_desktop_type", _desktop_type,
    "Type text into the focused window of the Instabox desktop. Never type secrets.", mutates=True, desktop=True)
instabox_desktop_press = registered("instabox_desktop_press", _desktop_press,
    "Press a key or key chord on the Instabox desktop.", mutates=True, desktop=True)
