"""Create independent conversations from saved assistant answers."""

from __future__ import annotations

import asyncio
import copy
import logging
import re
import shutil
import tempfile
import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from urllib.parse import quote

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START

from agent.bootstrap.container import require_active_container
from agent.modules.agent_runtime import get_active_session_registry, get_background_task_manager
from agent.modules.agent_runtime.active_sessions import ThreadMutationConflictError
from agent.modules.agents import get_catalog_service
from agent.modules.conversations.compaction import is_valid_message_sequence
from agent.modules.conversations.history import (
    CheckpointNotFoundError,
    forkable_message_indices,
    get_history_checkpointer,
    get_thread_messages_payload,
)
from agent.modules.conversations.repository import get_conversation_thread_repository
from agent.modules.projects import get_project_workspace_service
from agent.modules.workflows import get_workflow_graph
from agent.modules.workspaces import (
    TEMP_WORKSPACE_KEY,
    WorkspaceBinding,
    WorkspaceRef,
    bind_workspace_ref,
    create_temp_workspace,
    derive_workspace_scope,
    get_thread_workspace_repository,
    is_temp_workspace,
    remember_thread_workspace_ref,
    resolve_workspace_ref,
)
from agent.shared.thread_ids import resolve_thread_id

logger = logging.getLogger(__name__)
_TITLE_SUFFIX = re.compile(r"^(.*) \(([2-9]|[1-9][0-9]+)\)$")


class ConversationForkConflictError(RuntimeError):
    """The source conversation cannot currently be forked."""


def next_fork_title(title: str, titles: list[str]) -> str:
    """Choose the next numbered title while reserving space for its suffix."""
    title = title.strip() or "New conversation"
    match = _TITLE_SUFFIX.fullmatch(title)
    base = match[1] if match else title
    number = int(match[2]) if match else 1
    for existing in titles:
        candidate = _TITLE_SUFFIX.fullmatch(existing)
        if candidate and candidate[1] == base[:255 - len(f" ({candidate[2]})")]:
            number = max(number, int(candidate[2]))
    while True:
        number += 1
        suffix = f" ({number})"
        candidate_title = base[:255 - len(suffix)].rstrip() + suffix
        if candidate_title not in titles:
            return candidate_title


def _copy_directory(source: Path, target: Path) -> None:
    """Copy files without preserving links into the original workspace."""
    if not source.is_dir():
        raise FileNotFoundError("The source temporary workspace is missing.")
    shutil.copytree(source, target, dirs_exist_ok=True, symlinks=False)


def _copy_temp_workspace(source: WorkspaceRef, destination: WorkspaceRef) -> None:
    _copy_directory(Path(source.locator), Path(destination.locator))
    _copy_temp_storage(source, destination)


def _copy_temp_storage(source: WorkspaceRef, destination: WorkspaceRef) -> None:
    from agent.modules.tools.runtime.thread_storage import THREAD_STORAGE_DIRS, workspace_storage_root

    source_storage = workspace_storage_root(derive_workspace_scope(source))
    target_storage = workspace_storage_root(derive_workspace_scope(destination))
    if source_storage.exists():
        _copy_directory(source_storage, target_storage)
    for category in THREAD_STORAGE_DIRS:
        physical = Path(destination.locator) / ".k41-agent" / category
        if physical.is_dir():
            _copy_directory(physical, target_storage / category)


def _rebind_storage_references(value: Any, replacements: list[tuple[str, str]]) -> Any:
    """Rebind application storage paths and URLs in copied message/state data."""
    if isinstance(value, str):
        for source, destination in replacements:
            value = value.replace(source, destination)
        return value
    if isinstance(value, list):
        return [_rebind_storage_references(item, replacements) for item in value]
    if isinstance(value, tuple):
        return tuple(_rebind_storage_references(item, replacements) for item in value)
    if isinstance(value, dict):
        return {
            _rebind_storage_references(key, replacements): _rebind_storage_references(item, replacements)
            for key, item in value.items()
        }
    if hasattr(value, "model_copy"):
        return value.model_copy(update={
            key: _rebind_storage_references(getattr(value, key), replacements)
            for key in type(value).model_fields
        })
    return value


async def _run_file_operation(operation, *args) -> None:
    # A cancelled to_thread worker keeps running. Wait before deleting its target.
    worker = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        await asyncio.shield(worker)
    except asyncio.CancelledError:
        try:
            await worker
        finally:
            raise


async def _copy_cloud_temp(source: WorkspaceRef, destination: WorkspaceRef, projects) -> None:
    from agent.modules.projects import LocalProjectStorage

    staging = tempfile.TemporaryDirectory(prefix="k41-conversation-fork-")
    try:
        store = LocalProjectStorage(Path(staging.name))
        key = "0"
        manifest = await projects.transfer.capture(source, store, key)
        revision = await asyncio.to_thread(store.put_manifest, key, manifest)
        await _run_file_operation(projects.transfer.materialize, store, key, revision, Path(destination.locator))
        await _run_file_operation(_copy_temp_storage, source, destination)
    finally:
        await _run_file_operation(staging.cleanup)


def _retain_skill_snapshots(snapshots, state: dict[str, Any], thread_id: str) -> None:
    from agent.modules.tools import conversation_key

    for entry in state.get("active_skills", {}).values():
        if entry.get("execution_mode") == "source":
            continue
        version = entry.get("version", "")
        if re.fullmatch(r"[a-f0-9]{64}", version):
            snapshots.retain(snapshots.read(version), conversation_key(thread_id))


async def _seed_fork_history(graph, thread_id: str, state: dict[str, Any]) -> dict[str, Any]:
    """Import prompts separately so their parents preserve the preceding history."""
    config = await graph.aupdate_state(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
        {**state, "messages": []}, as_node=START,
    )
    messages = state["messages"]
    start = 0
    for index, message in enumerate(messages):
        if not isinstance(message, HumanMessage):
            continue
        if index > start:
            config = await graph.aupdate_state(config, {"messages": messages[start:index]}, as_node=START)
            config = await graph.aupdate_state(config, None, as_node=END)
        config = await graph.aupdate_state(config, {"messages": [message]}, as_node=START)
        start = index + 1
    if start < len(messages):
        config = await graph.aupdate_state(config, {"messages": messages[start:]}, as_node=START)
    return await graph.aupdate_state(config, None, as_node=END)


async def fork_conversation_thread(
    thread_id: str, *, source_checkpoint_id: str, message_index: int,
) -> dict[str, Any]:
    """Copy a completed answer into a fresh, idle conversation thread."""
    thread_id = resolve_thread_id(thread_id)
    try:
        with get_active_session_registry().reserve_thread_mutation(thread_id, require_idle=True):
            return await _fork_reserved(thread_id, source_checkpoint_id, message_index)
    except ThreadMutationConflictError as exc:
        raise ConversationForkConflictError("The conversation is busy. Please try again after it finishes.") from exc


async def _fork_reserved(thread_id: str, checkpoint_id: str, message_index: int) -> dict[str, Any]:
    repository = get_conversation_thread_repository()
    source = await repository.get(thread_id)
    if source is None:
        raise CheckpointNotFoundError("Conversation not found.")
    if source["kind"] not in {"user", "background"}:
        raise ValueError("This conversation type cannot be forked.")
    task = get_background_task_manager().get_by_thread_id(thread_id)
    if task and task.get("status") in {"pending", "running"}:
        raise ConversationForkConflictError("The conversation is still running.")
    agent = get_catalog_service().get_agent(source["agent_name"] or "default")
    if agent is None:
        raise ValueError("The conversation agent is no longer available.")
    graph = get_workflow_graph(agent.graph_type)
    source_config = {"configurable": {
        "thread_id": thread_id, "checkpoint_ns": "", "checkpoint_id": checkpoint_id,
    }}
    checkpointer = get_history_checkpointer()
    if await checkpointer.aget_tuple(source_config) is None:
        raise CheckpointNotFoundError("Checkpoint not found.")
    snapshot = await graph.aget_state(source_config)
    messages = snapshot.values.get("messages", [])
    if message_index not in forkable_message_indices(messages):
        raise ValueError("Choose a completed assistant answer to fork.")
    if snapshot.next and message_index == len(messages) - 1:
        # Fresh forks first seed state at START, then clear its scheduled work.
        # Accept the identical completed child, but reject intermediate model output.
        async for child in graph.aget_state_history({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}):
            parent = (child.parent_config or {}).get("configurable", {})
            if (parent.get("checkpoint_id") == checkpoint_id and not child.next
                    and child.values.get("messages") == messages):
                snapshot = child
                break
        else:
            raise ValueError("The selected assistant response has not completed its turn.")
    messages = messages[:message_index + 1]
    if not is_valid_message_sequence(messages):
        raise ValueError("The selected history contains an incomplete tool call.")

    workspaces = get_thread_workspace_repository()
    projects = get_project_workspace_service()
    binding = await projects.resolve_thread(thread_id)
    if binding is None:
        stored = await workspaces.get(thread_id)
        if stored and stored.get("workspace"):
            binding = WorkspaceBinding.model_validate(stored["workspace"])
        elif task and task.get("workspace"):
            binding = (await projects.find_binding(task["workspace"])
                       or bind_workspace_ref(resolve_workspace_ref(task["workspace"])))
    scope_key = TEMP_WORKSPACE_KEY if binding is None or is_temp_workspace(binding.execution) else binding.scope.key

    with ExitStack() as reservations:
        if binding and binding.project_workspace_id:
            _, _, runtime = await projects.rows(binding.project_id, binding.project_workspace_id)
            projects.switcher.assert_available(binding.project_workspace_id, runtime)
            reservations.enter_context(projects.switcher.run_lease(binding.project_workspace_id))
        elif binding:
            from agent.modules.workspaces.host_mirror import mirror_state, thread_storage_key, transition_active

            try:
                rows, _ = await projects.host_mirrors.thread_status(thread_id)
            except KeyError:
                rows = []
            if rows:
                if transition_active(mirror_state(rows[0]).get("transition")):
                    raise ConversationForkConflictError("The workspace is changing its execution environment.")
                reservations.enter_context(projects.switcher.run_lease(-int(thread_storage_key(rows))))
        new_id = uuid.uuid4().hex
        new_binding = binding
        temp = None
        skill_snapshots = None
        try:
            state = copy.deepcopy(snapshot.values)
            state["messages"] = copy.deepcopy(messages)
            replacements = [(f"thread_id={quote(thread_id, safe='')}", f"thread_id={new_id}")]
            if binding is None or is_temp_workspace(binding.execution):
                temp = await create_temp_workspace(new_id)
                new_binding = bind_workspace_ref(temp)
                if binding:
                    if binding.execution.backend == "local":
                        await _run_file_operation(_copy_temp_workspace, binding.execution, temp)
                    else:
                        await _copy_cloud_temp(binding.execution, temp, projects)
                    from agent.modules.tools.runtime.thread_storage import workspace_storage_root
                    from agent.modules.skills.context import activation_key
                    from agent.modules.skills.sources import workspace_key

                    paths = [
                        (str(workspace_storage_root(binding.scope)), str(workspace_storage_root(new_binding.scope))),
                        (binding.execution.locator if binding.execution.backend == "local"
                         else str(binding.execution.metadata.get("root") or "/workspace"), temp.locator),
                    ]
                    replacements += paths + [(old.replace("\\", "/"), new.replace("\\", "/")) for old, new in paths]
                    replacements.append((workspace_key(binding.execution), workspace_key(temp)))
                    for key, entry in state.get("active_skills", {}).items():
                        replacements.append((key, activation_key(entry["agent_name"], temp, entry["name"])))
            state = _rebind_storage_references(state, replacements)
            if state.get("active_skills"):
                from agent.modules.skills import get_skill_packages

                skill_snapshots = get_skill_packages().snapshots
                await _run_file_operation(_retain_skill_snapshots, skill_snapshots, state, new_id)
            await remember_thread_workspace_ref(new_id, new_binding)
            config = await _seed_fork_history(graph, new_id, state)
            payload_messages, active_id = await get_thread_messages_payload(
                new_id, checkpoint_id=config["configurable"]["checkpoint_id"], include_branch_metadata=True,
            )
            container = require_active_container()
            async with container._conversation_fork_lock:
                titles = await repository.list_workspace_titles(scope_key)
                metadata = await repository.upsert(
                    thread_id=new_id,
                    **{key: source[key] for key in (
                        "platform", "user_id", "channel_id", "agent_name", "provider", "model",
                    )},
                    title=next_fork_title(source["title"], titles), kind="user",
                )
            return {
                **metadata, "messages": payload_messages, "active_checkpoint_id": active_id,
                "workspace": new_binding.model_dump(), "pending_requests": [], "resume_error": None,
            }
        except BaseException:
            # Never run lifecycle cleanup against a shared project workspace.
            async def cleanup():
                for operation in (
                    lambda: repository.delete(new_id),
                    lambda: checkpointer.adelete_thread(new_id),
                    lambda: workspaces.delete(new_id),
                ):
                    try:
                        await operation()
                    except Exception:
                        logger.exception("Failed to clean up unpublished conversation %s", new_id)
                if skill_snapshots is not None:
                    from agent.modules.tools import conversation_key

                    try:
                        await asyncio.to_thread(skill_snapshots.release, conversation_key(new_id))
                    except Exception:
                        logger.exception("Failed to release skill snapshots for unpublished conversation %s", new_id)
                if temp is not None:
                    from agent.modules.tools.runtime.thread_storage import THREAD_STORAGE_BASE_DIR, workspace_storage_root
                    from agent.modules.workspaces.service import temp_workspace_root

                    for path in (Path(temp.locator), workspace_storage_root(new_binding.scope)):
                        target = path.resolve()
                        allowed = temp_workspace_root().resolve() if path == Path(temp.locator) else THREAD_STORAGE_BASE_DIR.resolve()
                        if target != allowed and target.is_relative_to(allowed):
                            await asyncio.to_thread(shutil.rmtree, target, True)

            await asyncio.shield(cleanup())
            raise
