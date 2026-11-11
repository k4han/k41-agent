"""Agent tools for Instabox machines, preview ports and snapshots.

Every operation is its own tool so agents can be granted each one separately.
Tools only reach the machine of the current workspace and the auxiliary
machines created in this conversation (see ``tools/runtime/targets.py``).
The desktop is driven by the shared ``computer_*`` tools.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any, Literal

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
from agent.modules.tools.permissions.risk import RiskLevel, classify
from agent.modules.tools.permissions.preparation import prepare_builtin
from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import AUXILIARY_MACHINES_TAG, ToolCapability, ToolCategory
from agent.modules.tools.result import ToolError, ToolErrorCode
from agent.modules.tools.runtime.targets import ExecutionTarget, forget_machine, owned_targets, resolve_target

BACKEND = "instabox"
Runtime = Annotated[ToolRuntime[Any, Any], InjectedToolArg]
MachineId = Annotated[
    str | None,
    Field(description="Machine ID (m_...) of the current workspace or of an auxiliary machine of this conversation. "
                      "Omit to use the machine of the current Instabox workspace."),
]
INSTABOX_TOOLS: dict[str, BaseTool] = {}


def _authorize(runtime, name: str, resource: str, values: dict[str, Any]) -> None:
    from agent.modules.tools.permissions import authorize_tool
    authorize_tool(runtime, name, resource, arguments=values)


async def _target(runtime, machine_id: str | None) -> ExecutionTarget:
    target = await resolve_target(runtime, machine_id, backend=BACKEND)
    if target.backend != BACKEND:
        raise ToolError(
            ToolErrorCode.INVALID_INPUT,
            "Pass machine_id: the current workspace does not run on an Instabox machine.",
        )
    return target


def _thread_id(runtime) -> str:
    from agent.modules.tools.runtime.context import get_thread_id

    return get_thread_id(runtime.config) or ""


def _owner(runtime) -> str:
    from agent.modules.tools.runtime.context import get_context_value

    return str(get_context_value(runtime.context, "browser_owner_id", None) or _thread_id(runtime) or "agent")


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


def registered(name: str, coroutine, description: str, *, mutates: bool = False, machines: bool = False) -> BaseTool:
    capabilities = [ToolCapability.NETWORK, ToolCapability.ASYNC_ONLY]
    if mutates:
        capabilities.append(ToolCapability.MUTATES_STATE)
    tags = ["instabox", AUXILIARY_MACHINES_TAG] if machines else ["instabox"]
    if machines:
        capabilities.append(ToolCapability.REQUIRES_THREAD)
    tool = register_tool(risk=classify(name, "*"), self_authorizing=True, prepare_permissions=prepare_builtin, may_interrupt=False, category=ToolCategory.SANDBOX, capabilities=capabilities, tags=tags)(
        StructuredTool.from_function(name=name, coroutine=coroutine, description=description)
    )
    INSTABOX_TOOLS[name] = tool
    return tool


# Auxiliary machines ------------------------------------------------------- #


def _ownership_metadata(runtime, root: str) -> dict[str, str]:
    from agent.modules.workspaces import root_thread_id

    return {"k41_created_by": "agent", "k41_role": "auxiliary",
            "k41_thread_id": root_thread_id(_thread_id(runtime)), "k41_root": root}


async def _reserve(runtime, purpose: str | None, root: str) -> int:
    from agent.modules.workspaces import ThreadMachineLimitError, reserve_thread_machine

    if not _thread_id(runtime):
        raise ToolError(ToolErrorCode.INVALID_INPUT, "Auxiliary machines belong to a conversation; this run has none.")
    limit = get_instabox_service().settings().max_machines_per_thread
    if limit <= 0:
        raise ToolError(ToolErrorCode.PERMISSION_DENIED,
                        "Agents may not create Instabox machines (workspace.instabox.max_machines_per_thread is 0).")
    try:
        return await reserve_thread_machine(_thread_id(runtime), BACKEND, limit=limit, purpose=purpose or "", root=root)
    except ThreadMachineLimitError as exc:
        raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from None


async def _settle(reservation: int, record: dict[str, Any], root: str) -> dict[str, Any]:
    """Bind the reservation to the new machine and prepare it for coding tools."""
    from agent.modules.workspaces import WorkspaceRef, activate_thread_machine, cancel_reservation, create_workspace_runtime_backend

    machine_id = str(record.get("machine_id") or "")
    if record.get("status") != "succeeded" or not machine_id:
        await asyncio.shield(cancel_reservation(reservation))
        return record
    await activate_thread_machine(reservation, machine_id, root=root)
    auxiliary: dict[str, Any] = {"target": machine_id, "root": root}
    try:
        backend = await create_workspace_runtime_backend(
            WorkspaceRef(backend=BACKEND, locator=machine_id, metadata={"root": root}))
        await backend.prepare()
        auxiliary["coding_tools"] = "ready"
        auxiliary["hint"] = (f"Pass target={machine_id} to bash, read, write, edit, grep, glob, list_dir, "
                             "process and computer_* tools. Files there are temporary until copied back with copy_files.")
    except Exception as exc:
        auxiliary["coding_tools"] = f"unavailable: {redact(str(exc))}"
        auxiliary["hint"] = "Port, snapshot and computer_* tools still work on this machine."
    return {**record, "auxiliary": auxiliary}


async def _create_reserved(reservation: int, create, root: str) -> dict[str, Any]:
    # The durable operation owns the reservation until its terminal result,
    # even if this waiter is cancelled or times out.
    record = await _call(create)
    return await _settle(reservation, record, root)


# Machines ---------------------------------------------------------------- #


async def _machine_list(runtime: Runtime):
    _authorize(runtime, "instabox_machine_list", "*", {})
    scope = {target.locator: target for target in await owned_targets(runtime, backend=BACKEND)}
    machines = await _call(get_instabox_service().list_machines())
    return _json([{**machine, "role": "auxiliary" if scope[machine["id"]].auxiliary else "workspace"}
                  for machine in machines if machine.get("id") in scope])


async def _machine_get(runtime: Runtime, machine_id: MachineId = None):
    target = await _target(runtime, machine_id)
    _authorize(runtime, "instabox_machine_get", target.locator, {})
    return _json(await _call(get_instabox_service().get_machine(target.locator)))


async def _machine_create(
    runtime: Runtime,
    name: Annotated[str | None, Field(description="Optional machine name: lowercase letters, digits and '-'.")] = None,
    template: Annotated[str | None, Field(description="Template ID. Defaults to the configured template.")] = None,
    snapshot_id: Annotated[str | None, Field(description="Start from a ready snapshot of this conversation instead of a template.")] = None,
    purpose: Annotated[str | None, Field(description="Short note on what the machine is for, shown to the user.")] = None,
):
    if snapshot_id:
        await _require_snapshot(runtime, snapshot_id)
    values = {"name": name, "template": template, "snapshot_id": snapshot_id, "purpose": purpose}
    _authorize(runtime, "instabox_machine_create", name or "*", values)
    service = get_instabox_service()
    root = service.settings().workspace_root
    reservation = await _reserve(runtime, purpose, root)
    metadata = _ownership_metadata(runtime, root)
    if snapshot_id:
        create = service.create_machine_from_snapshot(snapshot_id, name=name, metadata=metadata,
                                                      created_by=_owner(runtime), wait=True,
                                                      auxiliary_reservation_id=reservation)
    else:
        create = service.create_machine(name=name, template=template, metadata=metadata,
                                        created_by=_owner(runtime), wait=True, auxiliary_reservation_id=reservation)
    return _json(await _create_reserved(reservation, create, root))


def _action_tool(action: str):
    async def run(runtime: Runtime, machine_id: MachineId = None):
        target = await _target(runtime, machine_id)
        _authorize(runtime, f"instabox_machine_{action}", target.locator, {})
        result = await _call(get_instabox_service().machine_action(
            target.locator, action, created_by=_owner(runtime), wait=True,
        ))
        return _json(result)

    return run


async def _machine_fork(
    runtime: Runtime,
    machine_id: MachineId = None,
    name: Annotated[str | None, Field(description="Name of the new independent machine.")] = None,
    purpose: Annotated[str | None, Field(description="Short note on what the copy is for, shown to the user.")] = None,
):
    source = await _target(runtime, machine_id)
    _authorize(runtime, "instabox_machine_fork", source.locator, {"name": name, "purpose": purpose})
    root = source.root or get_instabox_service().settings().workspace_root
    reservation = await _reserve(runtime, purpose, root)
    record = await _create_reserved(reservation, get_instabox_service().fork_machine(
        source.locator, name=name, created_by=_owner(runtime), wait=True,
        auxiliary_reservation_id=reservation), root)
    if record.get("status") == "failed" and "fork_unavailable" in json.dumps(record.get("error") or {}):
        record["hint"] = "Fork is unavailable on this host. Create a snapshot, then create a machine from it."
    return _json(record)


async def _machine_delete(
    runtime: Runtime,
    machine_id: Annotated[str, Field(description="Exact ID of an auxiliary machine of this conversation.")],
    confirm: Annotated[str, Field(description="Repeat the exact machine ID to confirm deletion.")],
):
    target = await _target(runtime, machine_id)
    if not target.auxiliary:
        raise ToolError(ToolErrorCode.PERMISSION_DENIED,
                        "The machine of the current workspace can only be deleted from the dashboard.")
    _authorize(runtime, "instabox_machine_delete", target.locator, {"confirm": confirm})
    result = await _call(get_instabox_service().delete_machine(
        target.locator, confirm=confirm, created_by=_owner(runtime), wait=True,
    ))
    if result.get("status") == "succeeded":
        await forget_machine(BACKEND, target.locator, deleted=True)
    return _json(result)


# Ports ------------------------------------------------------------------- #


async def _port_list(runtime: Runtime, machine_id: MachineId = None):
    target = await _target(runtime, machine_id)
    _authorize(runtime, "instabox_port_list", target.locator, {})
    return _json(await _call(get_instabox_service().list_ports(target.locator)))


def _authorize_access(runtime, name: str, resource: str, access: str) -> None:
    _authorize(runtime, name, resource, {"access": access})
    if access == "public":
        # Opening a port to anyone asks by default, whatever rule allows the tool.
        _authorize(runtime, "instabox_port_public", resource, {})


async def _port_publish(
    runtime: Runtime,
    port: Annotated[int, Field(ge=1024, le=65535, description="HTTP port of a server running in the machine.")],
    machine_id: MachineId = None,
    access: Annotated[Literal["private", "public"], Field(description="private (default) needs a token link; public is open to anyone and asks the user.")] = "private",
    name: Annotated[str | None, Field(description="Optional display name.")] = None,
):
    target = await _target(runtime, machine_id)
    _authorize_access(runtime, "instabox_port_publish", f"{target.locator}:{port}", access)
    return _json(await _call(get_instabox_service().publish_port(target.locator, port, access=access, name=name)))


async def _port_set_access(
    runtime: Runtime,
    port: Annotated[int, Field(ge=1024, le=65535)],
    access: Literal["private", "public"],
    machine_id: MachineId = None,
):
    target = await _target(runtime, machine_id)
    _authorize_access(runtime, "instabox_port_set_access", f"{target.locator}:{port}", access)
    return _json(await _call(get_instabox_service().set_port_access(target.locator, port, access)))


async def _port_rotate_token(runtime: Runtime, port: Annotated[int, Field(ge=1024, le=65535)], machine_id: MachineId = None):
    target = await _target(runtime, machine_id)
    _authorize(runtime, "instabox_port_rotate_token", f"{target.locator}:{port}", {})
    return _json(await _call(get_instabox_service().rotate_port_token(target.locator, port)))


async def _port_close(runtime: Runtime, port: Annotated[int, Field(ge=1024, le=65535)], machine_id: MachineId = None):
    target = await _target(runtime, machine_id)
    _authorize(runtime, "instabox_port_close", f"{target.locator}:{port}", {})
    await _call(get_instabox_service().close_port(target.locator, port))
    return _json({"closed": True, "port": port})


# Snapshots --------------------------------------------------------------- #


async def _snapshot_scope(runtime) -> set[str]:
    """Snapshots of this conversation plus the configured default snapshot."""
    from agent.modules.workspaces import SNAPSHOT, list_thread_resources

    scope = {record.locator for record in await list_thread_resources(_thread_id(runtime), kind=SNAPSHOT, backend=BACKEND)}
    default = get_instabox_service().settings().snapshot_id
    if default:
        scope.add(default)
    return scope


async def _require_snapshot(runtime, snapshot_id: str) -> None:
    if snapshot_id not in await _snapshot_scope(runtime):
        raise ToolError(ToolErrorCode.PERMISSION_DENIED,
                        f"Snapshot {snapshot_id} was not created in this conversation and is not the configured default.")


async def _snapshot_list(runtime: Runtime):
    _authorize(runtime, "instabox_snapshot_list", "*", {})
    scope = await _snapshot_scope(runtime)
    snapshots = await _call(get_instabox_service().list_snapshots())
    return _json([snapshot for snapshot in snapshots if snapshot.get("id") in scope])


async def _snapshot_get(runtime: Runtime, snapshot_id: str):
    await _require_snapshot(runtime, snapshot_id)
    _authorize(runtime, "instabox_snapshot_get", snapshot_id, {})
    return _json(await _call(get_instabox_service().get_snapshot(snapshot_id)))


async def _snapshot_create(
    runtime: Runtime,
    name: Annotated[str, Field(description="Unique snapshot name: lowercase letters, digits and '-'.")],
    machine_id: MachineId = None,
    description: str | None = None,
):
    target = await _target(runtime, machine_id)
    _authorize(runtime, "instabox_snapshot_create", target.locator, {"name": name})
    record = await _call(get_instabox_service().create_snapshot(
        target.locator, name=name, description=description, created_by=_owner(runtime), wait=True,
        owner_thread_id=_thread_id(runtime), workspace_root=target.root,
    ))
    return _json(record)


async def _snapshot_delete(
    runtime: Runtime,
    snapshot_id: str,
    confirm: Annotated[str, Field(description="Repeat the exact snapshot ID to confirm deletion.")],
):
    from agent.modules.workspaces import SNAPSHOT, mark_resource_deleted

    await _require_snapshot(runtime, snapshot_id)
    _authorize(runtime, "instabox_snapshot_delete", snapshot_id, {"confirm": confirm})
    await _call(get_instabox_service().delete_snapshot(snapshot_id, confirm=confirm))
    await mark_resource_deleted(BACKEND, snapshot_id, kind=SNAPSHOT)
    return _json({"deleted": True, "snapshot_id": snapshot_id})


async def _snapshot_restore(
    runtime: Runtime,
    machine_id: Annotated[str, Field(description="Exact machine ID to restore in place.")],
    snapshot_id: Annotated[str, Field(description="Exact ID of a ready snapshot.")],
):
    target = await _target(runtime, machine_id)
    await _require_snapshot(runtime, snapshot_id)
    # Restore requires confirmation outside full_access, even with an allow rule.
    _authorize(runtime, "instabox_snapshot_restore", f"{target.locator}<-{snapshot_id}", {})
    return _json(await _call(get_instabox_service().restore_machine(
        target.locator, snapshot_id, confirm_machine=target.locator, confirm_snapshot=snapshot_id,
        created_by=_owner(runtime),
    )))


instabox_machine_list = registered("instabox_machine_list", _machine_list,
    "List the Instabox machines this conversation may use: the workspace machine and its auxiliary machines. Listing never wakes a machine.", machines=True)
instabox_machine_get = registered("instabox_machine_get", _machine_get,
    "Show one Instabox machine: state, size, template and labels. Never wakes the machine.", machines=True)
instabox_machine_create = registered("instabox_machine_create", _machine_create,
    "Create an auxiliary Instabox machine for this conversation and wait until it runs. Asks the user by default and is limited per conversation. "
    "Work on it by passing its ID as target to the coding and computer_* tools; its files are temporary until copied back with copy_files.", mutates=True, machines=True)
instabox_machine_start = registered("instabox_machine_start", _action_tool("start"),
    "Start a stopped Instabox machine and wait until it runs.", mutates=True, machines=True)
instabox_machine_stop = registered("instabox_machine_stop", _action_tool("stop"),
    "Stop a running Instabox machine. Running programs end; files are kept.", mutates=True, machines=True)
instabox_machine_suspend = registered("instabox_machine_suspend", _action_tool("suspend"),
    "Pause a running Instabox machine, keeping its running programs.", mutates=True, machines=True)
instabox_machine_resume = registered("instabox_machine_resume", _action_tool("resume"),
    "Resume a paused Instabox machine where it left off.", mutates=True, machines=True)
instabox_machine_fork = registered("instabox_machine_fork", _machine_fork,
    "Copy a running Instabox machine's disks into a new auxiliary machine of this conversation. Memory and running programs are not copied. Asks the user by default. If fork is unavailable, create a snapshot and start a machine from it.", mutates=True, machines=True)
instabox_machine_delete = registered("instabox_machine_delete", _machine_delete,
    "Permanently delete an auxiliary Instabox machine of this conversation and its storage. Copy needed files back first. Requires the exact machine ID twice and asks the user by default.", mutates=True, machines=True)

instabox_port_list = registered("instabox_port_list", _port_list,
    "List the published HTTPS preview ports of an Instabox machine.")
instabox_port_publish = registered("instabox_port_publish", _port_publish,
    "Give an HTTP port of an Instabox machine an HTTPS preview URL. Private by default: the returned link carries a token. Use this instead of localhost to view apps running in the machine.", mutates=True)
instabox_port_set_access = registered("instabox_port_set_access", _port_set_access,
    "Switch a published preview port between private and public access. Public access asks the user.", mutates=True)
instabox_port_rotate_token = registered("instabox_port_rotate_token", _port_rotate_token,
    "Issue a new token for a private preview port and end connections using the old link.", mutates=True)
instabox_port_close = registered("instabox_port_close", _port_close,
    "Remove a port's preview URL and close its active connections.", mutates=True)

instabox_snapshot_list = registered("instabox_snapshot_list", _snapshot_list,
    "List the Instabox snapshots of this conversation and the configured default snapshot, with their state (pending, ready or failed).")
instabox_snapshot_get = registered("instabox_snapshot_get", _snapshot_get,
    "Show one Instabox snapshot of this conversation.")
instabox_snapshot_create = registered("instabox_snapshot_create", _snapshot_create,
    "Save an Instabox machine's system files and storage as a snapshot and wait until it is ready.", mutates=True)
instabox_snapshot_delete = registered("instabox_snapshot_delete", _snapshot_delete,
    "Delete an Instabox snapshot of this conversation. Machines created from it are not affected. Asks the user by default.", mutates=True)
instabox_snapshot_restore = registered("instabox_snapshot_restore", _snapshot_restore,
    "Put an Instabox machine's disks back to a ready snapshot in place. Everything written since the snapshot is lost. Always asks the user to confirm the exact machine and snapshot; refuses while tasks or processes run on the machine.", mutates=True)
