"""Resolve delegated runs using durable checkpoint metadata and tool artifacts."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.types import Command


async def find_delegation(parent_thread_id: str, *, delegation_id=None, tool_call_id=None, parent_message_id=None):
    from agent.modules.workflows import find_delegation_checkpoint, get_checkpointer
    criteria = {"k41_parent_thread_id": parent_thread_id}
    if delegation_id:
        criteria["k41_delegation_id"] = delegation_id
    if tool_call_id:
        criteria["k41_parent_tool_call_id"] = tool_call_id
    if parent_message_id:
        criteria["k41_parent_message_id"] = parent_message_id
    return await find_delegation_checkpoint(get_checkpointer(), criteria)


def delegation_reference(checkpoint, *, status="completed") -> dict[str, Any]:
    metadata, config = checkpoint.metadata, checkpoint.config["configurable"]
    return {
        "delegation_id": metadata["k41_delegation_id"],
        "parent_thread_id": metadata["k41_parent_thread_id"],
        "tool_call_id": metadata["k41_parent_tool_call_id"],
        "agent_name": metadata["k41_agent_name"],
        "thread_id": config["thread_id"], "checkpoint_id": config["checkpoint_id"],
        "status": status,
    }


async def annotate_delegation_results(value, parent_thread_id: str):
    if isinstance(value, ToolMessage) and value.name == "delegate_task":
        try:
            checkpoint = await find_delegation(parent_thread_id, tool_call_id=value.tool_call_id)
        except RuntimeError:
            return value
        if checkpoint is not None:
            value = value.model_copy(update={"artifact": {
                **(value.artifact if isinstance(value.artifact, dict) else {}),
                "subagent": delegation_reference(checkpoint, status="failed" if value.status == "error" else "completed"),
            }})
        return value
    if isinstance(value, Command):
        from dataclasses import replace
        return replace(value, update=await annotate_delegation_results(value.update, parent_thread_id))
    if isinstance(value, dict) and "messages" in value:
        return {**value, "messages": await annotate_delegation_results(value["messages"], parent_thread_id)}
    if isinstance(value, list):
        return [await annotate_delegation_results(item, parent_thread_id) for item in value]
    return value


async def get_subagent_history(parent_thread_id: str, delegation_id: str):
    from agent.modules.conversations import get_thread_messages_payload
    checkpoint = await find_delegation(parent_thread_id, delegation_id=delegation_id)
    if checkpoint is None:
        return None
    reference = delegation_reference(checkpoint)
    from agent.modules.workflows import get_workflow_graph, graph_name_for_thread
    graph = get_workflow_graph(await graph_name_for_thread(reference["thread_id"]))
    state = await graph.aget_state(checkpoint.config)
    if any(task.error for task in state.tasks):
        reference["status"] = "failed"
    elif any(task.interrupts for task in state.tasks):
        reference["status"] = "interrupted"
    elif state.next:
        reference["status"] = "stopped"
    messages, checkpoint_id = await get_thread_messages_payload(
        reference["thread_id"], checkpoint_id=reference["checkpoint_id"],
    )
    return {**reference, "messages": messages, "active_checkpoint_id": checkpoint_id}


async def enrich_delegated_calls(messages: list[dict], parent_thread_id: str):
    """Recover links for interrupted calls that did not commit a ToolMessage."""
    completed = {message.get("tool_call_id") for message in messages if message.get("role") == "tool"}
    for message in messages:
        for call in message.get("tool_calls", []):
            if call.get("name") != "delegate_task" or call.get("id") in completed:
                continue
            try:
                checkpoint = await find_delegation(parent_thread_id, tool_call_id=call.get("id"),
                    parent_message_id=message.get("id"))
            except RuntimeError:
                continue
            if checkpoint is not None:
                from agent.modules.agent_runtime.active_sessions import get_active_session_registry
                active = any(session["thread_id"] == checkpoint.config["configurable"]["thread_id"]
                             for session in get_active_session_registry().list_active())
                call["subagent"] = delegation_reference(checkpoint, status="running" if active else "stopped")
    return messages


async def copy_delegated_history(state: dict, source_thread_id: str, target_thread_id: str,
        *, created=None, replacements=()):
    """Copy referenced child snapshots so deleting a source cannot break its fork."""
    from agent.modules.conversations import seed_fork_history, rebind_storage_references
    from agent.modules.workflows import get_workflow_graph, graph_name_for_thread, get_checkpointer
    import copy

    for message in state.get("messages", []):
        artifact = getattr(message, "artifact", None)
        reference = artifact.get("subagent") if isinstance(artifact, dict) else None
        if not reference:
            continue
        child_id = reference["thread_id"]
        if not child_id.startswith(source_thread_id + ":sub:"):
            continue
        graph = get_workflow_graph(await graph_name_for_thread(child_id))
        snapshot = await graph.aget_state({"configurable": {
            "thread_id": child_id, "checkpoint_ns": "", "checkpoint_id": reference["checkpoint_id"],
        }})
        if not snapshot or not snapshot.values:
            continue
        copied_id = target_thread_id + child_id[len(source_thread_id):]
        copied = rebind_storage_references(copy.deepcopy(snapshot.values), replacements)
        if created is not None:
            created.append(copied_id)
        await copy_delegated_history(copied, child_id, copied_id, created=created, replacements=replacements)
        config = await seed_fork_history(graph, copied_id, copied)
        saver = get_checkpointer()
        saved = await saver.aget_tuple(config)
        metadata = {**saved.metadata,
            **{key: value for key, value in (snapshot.metadata or {}).items() if key.startswith("k41_")},
            "k41_parent_thread_id": target_thread_id,
            "k41_parent_tool_call_id": reference["tool_call_id"],
            "k41_agent_name": reference["agent_name"], "k41_delegation_id": reference["delegation_id"]}
        # Re-store metadata on the already complete imported checkpoint.
        parent_config = saved.parent_config or {"configurable": {
            "thread_id": copied_id, "checkpoint_ns": "",
        }}
        await saver.aput(parent_config, saved.checkpoint, metadata, {})
        artifact["subagent"] = {**reference, "thread_id": copied_id,
            "parent_thread_id": target_thread_id, "checkpoint_id": config["configurable"]["checkpoint_id"]}
