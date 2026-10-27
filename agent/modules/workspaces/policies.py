"""Explicit synchronization policies for durable and temporary workspaces."""

from typing import Literal

WorkspaceSyncPolicy = Literal["manual_apply", "mirror_strict"]
MANUAL_APPLY = "manual_apply"
MIRROR_STRICT = "mirror_strict"


def sync_policy(row) -> WorkspaceSyncPolicy:
    # Unmigrated records and legacy adapters must retain their original behavior.
    value = getattr(row, "sync_policy", None)
    if value == MANUAL_APPLY:
        return MANUAL_APPLY
    if value in {None, MIRROR_STRICT}:
        return MIRROR_STRICT
    raise ValueError("Unsupported workspace synchronization policy.")
