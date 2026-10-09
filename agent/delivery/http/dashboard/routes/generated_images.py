from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from agent.delivery.http.dashboard.routes.helpers.workspace import workspace_ref_for_thread
from agent.modules.tools import (
    ensure_workspace_storage_root,
    get_generated_images_dir,
)
from agent.modules.workspaces import derive_workspace_scope

router = APIRouter()

ALLOWED_BINARY_EXTENSIONS = {
    # Images
    ".gif", ".jpeg", ".jpg", ".png", ".webp", ".svg", ".bmp", ".ico", ".tiff",
    # Documents & Spreadsheets
    ".xlsx", ".xls", ".csv", ".tsv", ".parquet", ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".odt", ".ods",
    # Data & Code & Text
    ".txt", ".md", ".json", ".xml", ".yaml", ".yml", ".log", ".sql", ".sqlite", ".db",
    # Archives
    ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar",
    # Media
    ".mp3", ".wav", ".ogg", ".m4a", ".mp4", ".webm", ".mov",
}
ALLOWED_IMAGE_EXTENSIONS = ALLOWED_BINARY_EXTENSIONS  # Backwards-compatible alias.
GENERATED_IMAGES_DIR = get_generated_images_dir()


async def resolve_storage_file(
    filename: str,
    *,
    category: str = "generated-images",
    thread_id: str | None = None,
) -> Path:
    if Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="File not found.")

    safe_category = Path(category).name
    root = GENERATED_IMAGES_DIR
    if thread_id and thread_id.strip():
        workspace = await workspace_ref_for_thread(thread_id, include_default=False)
        if workspace is not None:
            scope = derive_workspace_scope(workspace)
            root = ensure_workspace_storage_root(scope) / safe_category
        elif safe_category == "generated-images":
            root = GENERATED_IMAGES_DIR

    if safe_category != "generated-images" and root == GENERATED_IMAGES_DIR:
        workspace = await workspace_ref_for_thread(thread_id or "")
        if workspace is None:
            raise HTTPException(status_code=404, detail="File not found.")
        root = ensure_workspace_storage_root(derive_workspace_scope(workspace)) / safe_category

    root = root.resolve()
    path = (root / filename).resolve()
    if path.parent != root:
        raise HTTPException(status_code=404, detail="File not found.")
    if path.suffix.lower() not in ALLOWED_BINARY_EXTENSIONS:
        raise HTTPException(status_code=403, detail="File extension is not allowed.")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    return path


async def resolve_generated_image(filename: str, *, thread_id: str | None = None) -> Path:
    """Backwards-compatible resolver for generated images."""
    return await resolve_storage_file(filename, category="generated-images", thread_id=thread_id)


@router.get("/dashboard-api/generated-images/{filename}")
async def get_generated_image(
    filename: str,
    thread_id: str | None = Query(default=None),
) -> FileResponse:
    path = await resolve_generated_image(filename, thread_id=thread_id)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/dashboard-api/storage-files/{category}/{filename}")
async def get_storage_file(
    category: str,
    filename: str,
    thread_id: str | None = Query(default=None),
) -> FileResponse:
    """Download a file from workspace storage (uploads, assets, memory, generated-images, etc.)."""
    path = await resolve_storage_file(filename, category=category, thread_id=thread_id)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


__all__ = ["router", "resolve_generated_image", "resolve_storage_file", "ALLOWED_BINARY_EXTENSIONS"]
