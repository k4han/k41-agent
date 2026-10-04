"""Operation-level authorization with exact, thread-bound approval grants."""

from __future__ import annotations

import fnmatch
import json
import os
import logging
from typing import Any

from agent.modules.tools.coding.models import CodingError, InvocationContext, PermissionResume
from agent.modules.tools.coding.paths import PathPermissions
from agent.modules.tools.coding.storage import OutputStore, atomic_json, digest

logger = logging.getLogger(__name__)


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
    from agent.modules.workflows import get_workflow_graph, make_run_config
    config = make_run_config(thread_id=thread_id)
    if checkpoint_id:
        config["configurable"]["checkpoint_id"] = checkpoint_id
    try:
        snapshot = await get_workflow_graph("react_agent").aget_state(config)
        return [permission_request_event(item.value, str(item.id))
                for task in getattr(snapshot, "tasks", ())
                for item in getattr(task, "interrupts", ())
                if isinstance(item.value, dict) and item.value.get("type") == "permission_request"]
    except Exception:
        return []


class Permissions(PathPermissions):
    def __init__(self, storage: OutputStore) -> None:
        self.storage = storage

    def assert_allowed(self, context: InvocationContext, action: str, resource: str,
                       *, allow_interrupt: bool = True, **metadata: Any) -> None:
        effect = "ask" if action == "external_directory" else "allow"
        for rule in context.permission_rules:
            path_action = action in {"read", "edit", "external_directory"}
            subject = self.normalize_resource(context, resource) if path_action else resource
            pattern = self.normalize_resource(context, rule.resource) if path_action else rule.resource
            if fnmatch.fnmatchcase(action, rule.action) and fnmatch.fnmatchcase(subject, pattern):
                effect = rule.effect
        logger.info("Coding permission call=%s action=%s effect=%s resource_fingerprint=%s",
                    context.tool_call_id, action, effect, digest(resource), extra={"tool_call_id": context.tool_call_id,
                    "permission_action": action, "permission_effect": effect,
                    "resource_fingerprint": digest(resource)})
        if effect == "deny":
            raise CodingError("permission_denied", f"Policy denies {action}: {resource}")
        if effect == "allow":
            return
        identity = json.dumps([context.agent_name, action, resource, metadata,
                               [rule.model_dump() for rule in context.permission_rules]], sort_keys=True)
        grant_key = digest(identity)
        grant_path = self.storage.owner_dir(context) / f"{grant_key[:32]}.grant"
        once_path = self.storage.owner_dir(context) / f"{digest(context.message_id + context.tool_call_id + grant_key)[:32]}.once"
        if grant_path.is_file() or (context.tool_call_id and once_path.is_file()):
            return
        if not allow_interrupt:
            raise CodingError("permission_denied", "The resource requires approval after preparation; no operation was performed.")
        if not context.approval_supported or not context.thread_id or not context.tool_call_id:
            raise CodingError("approval_unavailable", "This channel does not support tool permission approval. Use the dashboard or configure an explicit allow/deny rule.")
        from langgraph.types import interrupt

        request_id = digest(f"{context.tool_call_id}\0{grant_key}")
        while True:
            response = interrupt({
                "type": "permission_request", "request_id": request_id,
                "tool_call_id": context.tool_call_id, "action": action,
                "resource": resource, "metadata": metadata,
            })
            approval = PermissionResume.model_validate(response)
            if approval.request_id == request_id:
                break
            # A replayed node may contain resume values for a previously
            # settled permission. They never authorize this new operation.
        if approval.decision == "deny":
            logger.info("Coding approval call=%s decision=deny resource_fingerprint=%s",
                        context.tool_call_id, digest(resource), extra={"tool_call_id": context.tool_call_id,
                        "permission_decision": "deny", "resource_fingerprint": digest(resource)})
            raise CodingError("permission_denied", f"User denied {action}: {resource}")
        logger.info("Coding approval call=%s decision=%s resource_fingerprint=%s",
                    context.tool_call_id, approval.decision, digest(resource), extra={"tool_call_id": context.tool_call_id,
                    "permission_decision": approval.decision, "resource_fingerprint": digest(resource)})
        if approval.decision == "allow_thread":
            atomic_json(grant_path, {"action": action, "resource": resource})
        else:
            atomic_json(once_path, {"action": action, "resource": resource})


    @staticmethod
    def normalize_resource(context: InvocationContext, resource: str) -> str:
        return (os.path.normcase(resource) if context.backend == "local" else resource).replace("\\", "/")
