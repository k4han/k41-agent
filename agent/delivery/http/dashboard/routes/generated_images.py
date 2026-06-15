from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from agent.delivery.http.dashboard.routes.helpers.workspace import workspace_ref_for_thread
from agent.modules.tools import generated_images_dir_for_workspace, get_generated_images_dir
from agent.modules.workspaces import derive_workspace_scope

router = APIRouter()

ALLOWED_IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
GENERATED_IMAGES_DIR = get_generated_images_dir()


async def resolve_generated_image(filename: str, *, thread_id: str | None = None) -> Path:
    if Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="Generated image not found.")

    root = GENERATED_IMAGES_DIR
    if thread_id and thread_id.strip():
        workspace = await workspace_ref_for_thread(thread_id, include_default=False)
        if workspace is not None:
            root = generated_images_dir_for_workspace(
                derive_workspace_scope(workspace),
                create=False,
            )
    root = root.resolve()
    path = (root / filename).resolve()
    if path.parent != root or path.suffix.lower() not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=404, detail="Generated image not found.")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Generated image not found.")
    return path


@router.get("/dashboard-api/generated-images/{filename}")
async def get_generated_image(
    filename: str,
    thread_id: str | None = Query(default=None),
) -> FileResponse:
    path = await resolve_generated_image(filename, thread_id=thread_id)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


__all__ = ["router", "resolve_generated_image"]
