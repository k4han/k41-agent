import logging
from agent.modules.workflows.message_history import normalize_messages_for_chat_model
from agent.shared.thread_ids import resolve_thread_id
from typing import Any

from agent.modules.workflows.run_config import (
    DEFAULT_CONTEXT_COMPACT_THRESHOLD,
    DEFAULT_WORKING_DIR,
    make_context as make_run_context,
    make_config as make_run_config,
)
from agent.modules.workflows.constants import (
    REACT_AGENT_GRAPH_TYPE,
    ROUTER_GRAPH_TYPE,
)


logger = logging.getLogger(__name__)


def register_builtin_workflows() -> None:
    from agent.modules.workflows.register_builtin_workflows import (
        register_builtin_workflows as _register_builtin_workflows,
    )

    _register_builtin_workflows()


def get_workflow_graph(name: str):
    from agent.modules.workflows.registry import GraphRegistry

    return GraphRegistry.get(name)


def list_registered_workflows() -> list[str]:
    from agent.modules.workflows.registry import GraphRegistry

    return list(GraphRegistry.all().keys())


async def initialize_checkpointer(container=None) -> Any:
    from agent.modules.workflows.checkpoint.store import (
        initialize_checkpointer as _initialize_checkpointer,
    )

    return await _initialize_checkpointer(container=container)


async def close_checkpointer(container=None) -> None:
    from agent.modules.workflows.checkpoint.store import (
        close_checkpointer as _close_checkpointer,
    )

    await _close_checkpointer(container=container)


def get_checkpointer():
    from agent.modules.workflows.checkpoint.store import get_checkpointer as _get_checkpointer

    return _get_checkpointer()


async def delete_workflow_thread(thread_id: str) -> None:
    thread_id = resolve_thread_id(thread_id)
    from agent.modules.agent_runtime import get_active_session_registry
    from agent.modules.workflows.checkpoint.store import get_checkpointer

    checkpointer = get_checkpointer()
    if checkpointer is not None:
        async with get_active_session_registry().wait_for_thread_mutation(thread_id):
            await checkpointer.adelete_thread(thread_id)


def _checkpoint_tuple_thread_id(checkpoint_tuple: object) -> str:
    tuple_config = getattr(checkpoint_tuple, "config", {}) or {}
    if not isinstance(tuple_config, dict):
        return ""

    configurable = tuple_config.get("configurable", {})
    if not isinstance(configurable, dict):
        return ""

    return str(configurable.get("thread_id", "") or "")


async def _list_workflow_child_thread_ids(checkpointer: object, thread_id: str) -> set[str]:
    thread_id = resolve_thread_id(thread_id)
    child_prefix = f"{thread_id}:sub:"
    child_thread_ids: set[str] = set()
    alist = getattr(checkpointer, "alist", None)
    if not callable(alist):
        logger.warning("Cannot list child workflow threads: checkpointer has no alist API.")
        return child_thread_ids

    async for checkpoint_tuple in alist(None):
        child_thread_id = _checkpoint_tuple_thread_id(checkpoint_tuple)
        if child_thread_id.startswith(child_prefix):
            child_thread_ids.add(child_thread_id)

    return child_thread_ids


async def delete_workflow_thread_tree(thread_id: str) -> None:
    thread_id = resolve_thread_id(thread_id)
    from agent.modules.agent_runtime import get_active_session_registry

    async with get_active_session_registry().wait_for_thread_mutation(thread_id):
        await _delete_workflow_thread_tree_reserved(thread_id)


async def _delete_workflow_thread_tree_reserved(thread_id: str) -> None:
    from agent.modules.agent_runtime import get_active_session_registry
    from agent.modules.tools import clear_conversation_storage
    from agent.modules.workflows.checkpoint.store import get_checkpointer

    await clear_conversation_storage(thread_id)
    checkpointer = get_checkpointer()
    thread_ids = {thread_id}
    try:
        thread_ids.update(await _list_workflow_child_thread_ids(checkpointer, thread_id))
    except Exception as exc:
        logger.warning(
            "Failed to list child workflow threads for %s: %s",
            thread_id,
            exc,
        )

    parent_error: Exception | None = None
    for target_thread_id in sorted(thread_ids, key=lambda value: (value == thread_id, value)):
        try:
            if target_thread_id == thread_id:
                await checkpointer.adelete_thread(target_thread_id)
            else:
                async with get_active_session_registry().wait_for_thread_mutation(target_thread_id):
                    await checkpointer.adelete_thread(target_thread_id)
        except Exception as exc:
            logger.warning(
                "Failed to delete workflow thread %s: %s",
                target_thread_id,
                exc,
            )
            if target_thread_id == thread_id:
                parent_error = exc

    if parent_error is not None:
        raise parent_error


__all__ = [
    "DEFAULT_CONTEXT_COMPACT_THRESHOLD",
    "normalize_messages_for_chat_model",
    "DEFAULT_WORKING_DIR",
    "delete_workflow_thread",
    "delete_workflow_thread_tree",
    "get_workflow_graph",
    "initialize_checkpointer",
    "get_checkpointer",
    "list_registered_workflows",
    "make_run_context",
    "make_run_config",
    "register_builtin_workflows",
    "close_checkpointer",
    "REACT_AGENT_GRAPH_TYPE",
    "ROUTER_GRAPH_TYPE",
]
