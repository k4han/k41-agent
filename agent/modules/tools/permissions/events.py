"""Permission interrupt projections."""

from typing import Any

def permission_request_event(value: dict[str, Any], interrupt_id: str = "") -> dict[str, Any]:
    """Project a permission interrupt onto the existing dashboard choice UI."""
    return {
        "type": "user_input_request", "tool_call_id": value.get("tool_call_id"),
        "interrupt_id": interrupt_id, "title": "Tool permission approval",
        "questions": [{
            "id": "permission:" + str(value["request_id"]),
            "question": f"{value['action']}: {value['resource']}\n{value.get('metadata', {})}",
            "selection_mode": "single", "required": True,
            "options": [{"id": "allow_once", "label": "Allow once"},
                        {"id": "allow_thread", "label": "Allow in this conversation"},
                        {"id": "deny", "label": "Deny"}],
            "free_text": {"enabled": False},
        }], "submit_label": "Apply decision",
    }


async def pending_permission_requests(thread_id: str, checkpoint_id: str | None = None) -> list[dict[str, Any]]:
    from agent.modules.acp import PERMISSION_INTERRUPT_TYPE, permission_request_event as acp_permission_event
    from agent.modules.workflows import get_workflow_graph, graph_name_for_thread, make_run_config
    config = make_run_config(thread_id=thread_id)
    if checkpoint_id:
        config["configurable"]["checkpoint_id"] = checkpoint_id
    projections = {"permission_request": permission_request_event, PERMISSION_INTERRUPT_TYPE: acp_permission_event}
    try:
        snapshot = await get_workflow_graph(await graph_name_for_thread(thread_id)).aget_state(config)
        return [projections[item.value["type"]](item.value, str(item.id))
                for task in getattr(snapshot, "tasks", ())
                for item in getattr(task, "interrupts", ())
                if isinstance(item.value, dict) and item.value.get("type") in projections]
    except Exception:
        return []


