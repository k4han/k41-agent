"""Dependency-free async task references shared by graph and tool policies."""

from typing import Any

ASYNC_TOOL_NAMES = frozenset({
    "start_async_task", "check_async_task", "list_async_tasks",
    "update_async_task", "cancel_async_task",
})
DELEGATION_TOOL_NAMES = ASYNC_TOOL_NAMES | {"delegate_task", "call_agent"}
LIVE_STATUSES = frozenset({"pending", "running", "waiting"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "timed_out"})


def is_subagent_run(config: dict) -> bool:
    thread_id = str(config.get("configurable", {}).get("thread_id") or "")
    return (bool(config.get("metadata", {}).get("k41_parent_thread_id"))
            or ":sub:" in thread_id or thread_id.startswith("sub_"))


def merge_task_references(left: dict | None, right: dict | None) -> dict:
    merged = dict(left or {})
    for key, value in (right or {}).items():
        if value.get("version", 0) >= merged.get(key, {}).get("version", 0):
            merged[key] = value
    return merged


def merge_event_cursors(left: dict | None, right: dict | None) -> dict:
    merged = dict(left or {})
    for key, value in (right or {}).items():
        merged[key] = max(merged.get(key, 0), value)
    return merged


def task_reference(task: dict[str, Any]) -> dict[str, Any]:
    keys = ("task_id", "agent_name", "parent_thread_id", "root_thread_id", "thread_id", "tool_call_id",
            "checkpoint_id", "status", "version", "run_id", "updated_at", "read_only")
    return {**{key: task.get(key) for key in keys},
            "delegation_id": task["task_id"], "execution_mode": "async"}


def async_wakeup_references(messages) -> dict[int, dict[str, Any]]:
    """Group delivered notifications by execution, including across history pages."""
    groups, references = {}, {}
    previous = None
    for index, message in enumerate(messages):
        metadata = getattr(message, "additional_kwargs", {})
        if metadata.get("source") != "async_subagent":
            previous = None
            continue
        wakeup_id = metadata.get("async_wakeup_id") or previous or f"async-event:{message.id}"
        group = groups.setdefault(wakeup_id, {"wakeup_id": wakeup_id, "agent_names": []})
        name = metadata.get("async_agent_name")
        if name and name not in group["agent_names"]:
            group["agent_names"].append(name)
        references[index] = group
        previous = wakeup_id
    return references


def request_key(*parts: str) -> str:
    import hashlib
    import json

    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()
