"""Runtime configuration for the Instabox integration (``workspace.instabox.*``)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

DEFAULT_INSTABOX_API_URL = "https://api.instabox.sh"
DEFAULT_INSTABOX_ROOT = "/data/home/workspace"
DEFAULT_INSTABOX_TEMPLATE = "agent-browser"
DEFAULT_DESKTOP_WIDTH = 1280
DEFAULT_DESKTOP_HEIGHT = 800
DEFAULT_MAX_MACHINES_PER_THREAD = 2


@dataclass(frozen=True)
class InstaboxSettings:
    enabled: bool = False
    api_key: str = ""
    api_url: str = DEFAULT_INSTABOX_API_URL
    workspace_root: str = DEFAULT_INSTABOX_ROOT
    template: str = DEFAULT_INSTABOX_TEMPLATE
    snapshot_id: str = ""
    org_id: str = ""
    vcpus: int = 0
    mem_mib: int = 0
    auto_suspend_after_seconds: int = 0
    max_machines_per_thread: int = DEFAULT_MAX_MACHINES_PER_THREAD

    @property
    def fingerprint(self) -> tuple[str, str]:
        """Identity used to decide whether the shared HTTP client must be rebuilt."""
        digest = hashlib.sha256(self.api_key.encode("utf-8")).hexdigest() if self.api_key else ""
        return self.api_url, digest

    def create_payload(self, *, snapshot_id: str | None = None) -> dict[str, Any]:
        """Build a create body; empty resource fields fall back to service defaults."""
        payload: dict[str, Any] = {}
        source_snapshot = (snapshot_id or self.snapshot_id or "").strip()
        if source_snapshot:
            payload["snapshotId"] = source_snapshot
        elif self.template:
            payload["templateId"] = self.template
        if self.org_id:
            payload["orgId"] = self.org_id
        if self.vcpus > 0 and self.mem_mib > 0:
            payload["vcpus"] = self.vcpus
            payload["memMiB"] = self.mem_mib
        if self.auto_suspend_after_seconds > 0:
            payload["autoSuspendAfterSeconds"] = self.auto_suspend_after_seconds
        return payload


def load_instabox_settings() -> InstaboxSettings:
    from agent.shared.config.service import get_config_service

    service = get_config_service()
    api_url = service.get_str("workspace.instabox.api_url", DEFAULT_INSTABOX_API_URL).strip()
    root = service.get_str("workspace.instabox.workspace_root", DEFAULT_INSTABOX_ROOT).strip()
    template = service.get_str("workspace.instabox.template", DEFAULT_INSTABOX_TEMPLATE).strip()
    return InstaboxSettings(
        enabled=service.get_bool("workspace.instabox.enabled", False),
        api_key=service.get_str("workspace.instabox.api_key", "").strip(),
        api_url=(api_url or DEFAULT_INSTABOX_API_URL).rstrip("/"),
        workspace_root=root or DEFAULT_INSTABOX_ROOT,
        template=template or DEFAULT_INSTABOX_TEMPLATE,
        snapshot_id=service.get_str("workspace.instabox.snapshot_id", "").strip(),
        org_id=service.get_str("workspace.instabox.org_id", "").strip(),
        vcpus=max(0, service.get_int("workspace.instabox.vcpus", 0)),
        mem_mib=max(0, service.get_int("workspace.instabox.mem_mib", 0)),
        auto_suspend_after_seconds=max(
            0, service.get_int("workspace.instabox.auto_suspend_after_seconds", 0)
        ),
        max_machines_per_thread=max(
            0, service.get_int("workspace.instabox.max_machines_per_thread", DEFAULT_MAX_MACHINES_PER_THREAD)
        ),
    )


__all__ = [
    "DEFAULT_DESKTOP_HEIGHT",
    "DEFAULT_DESKTOP_WIDTH",
    "DEFAULT_INSTABOX_API_URL",
    "DEFAULT_INSTABOX_ROOT",
    "DEFAULT_INSTABOX_TEMPLATE",
    "DEFAULT_MAX_MACHINES_PER_THREAD",
    "InstaboxSettings",
    "load_instabox_settings",
]
