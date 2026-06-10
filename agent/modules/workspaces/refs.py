from __future__ import annotations

from pathlib import Path, PureWindowsPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_LOCAL_WORKSPACE = str(Path.home() / "k41-agent")


# ``str`` (rather than a Literal of built-in backends) so plugin
# backends registered through the registry don't require updating this alias.
# Validation happens at runtime via ``is_registered_workspace_backend``.
WorkspaceBackendName = str


class WorkspaceRef(BaseModel):
    """Serializable reference to a workspace backend instance."""

    backend: WorkspaceBackendName = "local"
    locator: str
    label: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    def display_label(self) -> str:
        if self.backend != "local":
            repository = str(
                self.metadata.get("repository_full_name") or ""
            ).strip()
            if repository:
                return repository
            label = self.label.strip()
            if label:
                return label
            root = str(self.metadata.get("root") or "").strip()
            suffix = f":{root}" if root else ""
            return f"{self.backend}:{self.locator}{suffix}"

        # 1. Check metadata for repository info
        repository = self.metadata.get("repository_full_name") or self.metadata.get(
            "repository"
        )
        if repository and isinstance(repository, str) and repository.strip():
            return repository.strip()

        # 2. Always show the basename (last folder) the user picked as the root.
        locator = self.locator.strip()
        label = self.label.strip()

        path_to_resolve = locator or label
        if not path_to_resolve:
            return ""

        # Keep custom (non-absolute) labels verbatim.
        if label and not _same_local_path(label, locator):
            if _is_absolute_local_path(label):
                return _local_workspace_display_label(label)
            return label
        return _local_workspace_display_label(locator or label)


def _local_workspace_display_label(value: str) -> str:
    trimmed = value.strip()
    normalized = trimmed.replace("\\", "/").rstrip("/")
    parts = [part for part in normalized.split("/") if part]
    if not parts:
        return trimmed

    leaf = parts[-1]
    start = len(parts) - 1
    while start > 0 and parts[start - 1].casefold() == leaf.casefold():
        start -= 1
    visible_parts = parts[start:] if start < len(parts) - 1 else [leaf]
    return f"{'/'.join(visible_parts)}/"


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
    from agent.modules.workspaces.registry import is_registered_workspace_backend

    if not is_registered_workspace_backend(backend):
        raise ValueError(f"Unsupported workspace backend: {backend}")

    raw_locator = str(data.get("locator") or default_locator).strip()
    if not raw_locator:
        raw_locator = default_locator

    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    raw_label = label if label is not None else data.get("label")

    if backend != "local":
        locator = raw_locator.strip()
        if not locator:
            raise ValueError(f"{backend.title()} sandbox ID is required.")
        normalized_metadata = dict(metadata)
        default_root = "workspace" if backend == "daytona" else "/workspace"
        root = (
            str(normalized_metadata.get("root") or default_root).strip()
            or default_root
        )
        normalized_metadata["root"] = root
        normalized_label = str(raw_label or "").strip() or f"{backend}:{locator}"
        return WorkspaceRef(
            backend=backend,
            locator=locator,
            label=normalized_label,
            metadata=normalized_metadata,
        )

    # Expand compact dashboard paths back into the configured workspace root.
    if raw_locator == "workspace" or raw_locator.startswith("workspace/"):
        from agent.shared.config.service import get_config_service

        try:
            workspace_root = (
                get_config_service()
                .get_path("workspace.root", "~/k41-agent")
                .expanduser()
                .resolve()
            )
        except Exception:
            workspace_root = Path("~/k41-agent").expanduser().resolve()

        if raw_locator == "workspace":
            raw_locator = str(workspace_root)
        else:
            rel_part = raw_locator[len("workspace/") :]
            rel_part = rel_part.strip().replace("\\", "/").strip("/")
            if rel_part:
                raw_locator = str(workspace_root / rel_part)
            else:
                raw_locator = str(workspace_root)

    locator = str(Path(raw_locator).expanduser().resolve())

    normalized_label = str(raw_label or "").strip() or locator

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

    normalized_backend = (backend or "local").strip().lower()
    from agent.modules.workspaces.registry import is_registered_workspace_backend

    if not is_registered_workspace_backend(normalized_backend):
        normalized_backend = "local"

    return WorkspaceRef(
        backend=normalized_backend,
        locator=locator,
        label=(label or "").strip() or locator,
        metadata=parsed_metadata,
    )


__all__ = [
    "DEFAULT_LOCAL_WORKSPACE",
    "WorkspaceRef",
    "WorkspaceBackendName",
    "normalize_workspace_ref",
    "workspace_ref_from_columns",
]
