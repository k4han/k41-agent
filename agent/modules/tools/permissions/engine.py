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

from .risk import ALWAYS_CONFIRM, RiskLevel, classify, decide

logger = logging.getLogger(__name__)


def evaluate(context: InvocationContext, action: str, resource: str,
             *, risk: RiskLevel | str | None = None) -> str:
    """Evaluate policy without consulting grants, storage or interrupts."""
    classified = classify(action, resource)
    if classified == RiskLevel.BLOCKED or risk == RiskLevel.BLOCKED:
        return "deny"
    path_action = action in {"read", "edit", "external_directory"}
    subject = Permissions.normalize_resource(context, resource) if path_action else resource
    effect = None
    for rule in context.permission_rules:
        pattern = Permissions.normalize_resource(context, rule.resource) if path_action else rule.resource
        if fnmatch.fnmatchcase(action, rule.action) and fnmatch.fnmatchcase(subject, pattern):
            if rule.effect == "deny":
                effect = "deny"
                break
            effect = rule.effect
    return decide(context.permission_mode, risk or classified, effect,
                  always_confirm=action in ALWAYS_CONFIRM)


class Permissions(PathPermissions):
    def __init__(self, storage: OutputStore) -> None:
        self.storage = storage

    def assert_allowed(self, context: InvocationContext, action: str, resource: str,
                       *, risk: RiskLevel | str | None = None, allow_interrupt: bool = True, details: dict[str, Any] | None = None,
                       **metadata: Any) -> None:
        """Apply rules and ask the user when needed.

        ``metadata`` is part of what an "allow for this conversation" grant
        covers; ``details`` is only shown in the approval request, so values
        such as click coordinates do not make every call ask again.
        """
        effect = evaluate(context, action, resource, risk=risk)
        logger.info("Coding permission call=%s action=%s effect=%s resource_fingerprint=%s",
                    context.tool_call_id, action, effect, digest(resource), extra={"tool_call_id": context.tool_call_id,
                    "permission_action": action, "permission_effect": effect,
                    "resource_fingerprint": digest(resource)})
        if effect == "deny":
            raise CodingError("permission_denied", f"Policy denies {action}: {resource}")
        if effect == "allow":
            return
        grant_key = self.grant_key(context, action, resource, metadata)
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
                "resource": resource, "metadata": {**(details or {}), **metadata},
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
    def grant_key(context, action, resource, metadata):
        identity = json.dumps([context.agent_name, action, resource, metadata,
                               [rule.model_dump() for rule in context.permission_rules]], sort_keys=True)
        return digest(identity)

    @staticmethod
    def normalize_resource(context: InvocationContext, resource: str) -> str:
        return (os.path.normcase(resource) if context.backend == "local" else resource).replace("\\", "/")
