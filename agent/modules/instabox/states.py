"""Normalize Instabox machine states to the workspace status vocabulary."""

from __future__ import annotations

from typing import Any

from agent.modules.instabox.errors import InstaboxApiError

TRANSITIONAL_STATES = frozenset({"creating", "starting", "stopping", "suspending", "resuming", "deleting"})
STATE_MAP = {
    "running": "started",
    "suspended": "paused",
    "stopped": "stopped",
    "deleted": "destroyed",
    "error": "error",
}


def normalize_machine_state(state: str | None) -> str:
    """Map a raw ``Machine.state`` to ``started|paused|stopped|destroyed|pending|error|unknown``."""
    value = str(state or "").strip().lower()
    if value in STATE_MAP:
        return STATE_MAP[value]
    if value in TRANSITIONAL_STATES:
        return "pending"
    return "unknown"


def status_from_error(exc: BaseException) -> str:
    """Only a definite not-found answer means the machine is gone."""
    if isinstance(exc, InstaboxApiError) and exc.is_not_found:
        return "destroyed"
    return "unknown"


def machine_summary(machine: dict[str, Any]) -> dict[str, Any]:
    spec = machine.get("spec") if isinstance(machine.get("spec"), dict) else {}
    runtime = machine.get("runtime") if isinstance(machine.get("runtime"), dict) else {}
    template = machine.get("template")
    if isinstance(template, dict):
        template = template.get("id") or template.get("name")
    snapshot = machine.get("snapshot")
    if isinstance(snapshot, dict):
        snapshot = snapshot.get("id") or snapshot.get("name")
    return {
        "id": machine.get("id"),
        "name": machine.get("name"),
        "state": machine.get("state"),
        "status": normalize_machine_state(machine.get("state")),
        "desired_state": machine.get("desiredState"),
        "operation_id": machine.get("operationId"),
        "template": template,
        "snapshot": snapshot,
        "vcpus": spec.get("vcpus"),
        "mem_mib": spec.get("memMiB"),
        "disk_gib": spec.get("persistentDiskGiB"),
        "ready": bool(runtime.get("ready")),
        "org_id": machine.get("orgId"),
        "metadata": machine.get("metadata") or {},
        "auto_suspend_after_seconds": machine.get("autoSuspendAfterSeconds"),
        "last_error": machine.get("lastError"),
        "created_at": machine.get("createdAt"),
        "updated_at": machine.get("updatedAt"),
    }


__all__ = ["machine_summary", "normalize_machine_state", "status_from_error"]
