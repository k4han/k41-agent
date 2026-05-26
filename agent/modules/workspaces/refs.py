from __future__ import annotations

from pathlib import Path, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_LOCAL_WORKSPACE = str(Path.home() / "kaka-agent")


class WorkspaceRef(BaseModel):
    """Serializable reference to a workspace backend instance."""

    backend: Literal["local"] = "local"
    locator: str
    label: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    def display_label(self) -> str:
        label = self.label.strip()
        locator = self.locator.strip()
        if label and not _same_local_path(label, locator):
            if _is_absolute_local_path(label):
                return _local_workspace_display_label(label)
            return label
        return _local_workspace_display_label(locator or label)


def _local_workspace_display_label(value: str) -> str:
    trimmed = value.strip()
    normalized = trimmed.replace("\\", "/").rstrip("/")
    parts = [part for part in normalized.split("/") if part]
    return f"{parts[-1]}/" if parts else trimmed


def _is_absolute_local_path(value: str) -> bool:
    trimmed = value.strip()
    return (
        trimmed.startswith("~")
        or Path(trimmed).expanduser().is_absolute()
        or PureWindowsPath(trimmed).is_absolute()
    )


def _same_local_path(left: str, right: str) -> bool:
    if not left or not right:
        return False
    try:
        left_path = Path(left).expanduser().resolve()
        right_path = Path(right).expanduser().resolve()
    except (OSError, RuntimeError):
        return left == right
    return left_path == right_path


def _model_dump(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        data = dump()
        return dict(data) if isinstance(data, dict) else {}
    return {}


def normalize_workspace_ref(
    workspace: WorkspaceRef | dict[str, Any] | str | None,
    *,
    default_locator: str,
    label: str | None = None,
) -> WorkspaceRef:
    """Normalize supported workspace inputs into a canonical WorkspaceRef."""
    if isinstance(workspace, WorkspaceRef):
        data = workspace.model_dump()
    elif isinstance(workspace, str):
        data = {"backend": "local", "locator": workspace}
    elif workspace is None:
        data = {"backend": "local", "locator": default_locator}
    else:
        data = _model_dump(workspace)

    backend = str(data.get("backend") or "local").strip().lower()
    if backend != "local":
        raise ValueError(f"Unsupported workspace backend: {backend}")

    raw_locator = str(data.get("locator") or default_locator).strip()
    if not raw_locator:
        raw_locator = default_locator
    locator = str(Path(raw_locator).expanduser().resolve())

    raw_label = label if label is not None else data.get("label")
    normalized_label = str(raw_label or "").strip() or locator
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    return WorkspaceRef(
        backend="local",
        locator=locator,
        label=normalized_label,
        metadata=dict(metadata),
    )


def workspace_ref_from_columns(
    *,
    backend: str | None,
    locator: str,
    label: str | None,
    metadata_json: str | None,
) -> WorkspaceRef:
    """Build a WorkspaceRef from already-normalized DB column values.

    DB locators are written by ``normalize_workspace_ref`` and therefore
    already absolute/expanded; skip the filesystem resolve to avoid syscalls
    on every row deserialization.
    """
    import json

    parsed_metadata: dict[str, Any] = {}
    if metadata_json:
        try:
            data = json.loads(metadata_json)
        except (TypeError, ValueError):
            data = None
        if isinstance(data, dict):
            parsed_metadata = data

    return WorkspaceRef(
        backend="local" if (backend or "local").strip().lower() == "local" else "local",
        locator=locator,
        label=(label or "").strip() or locator,
        metadata=parsed_metadata,
    )


__all__ = [
    "DEFAULT_LOCAL_WORKSPACE",
    "WorkspaceRef",
    "normalize_workspace_ref",
    "workspace_ref_from_columns",
]
