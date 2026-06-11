from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from agent.modules.tools.builtin.image.generate_image import GENERATED_IMAGES_DIR

router = APIRouter()

ALLOWED_IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}


def resolve_generated_image(filename: str) -> Path:
    if Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="Generated image not found.")

    root = GENERATED_IMAGES_DIR.resolve()
    path = (root / filename).resolve()
    if path.parent != root or path.suffix.lower() not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=404, detail="Generated image not found.")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Generated image not found.")
    return path


@router.get("/dashboard-api/generated-images/{filename}")
async def get_generated_image(filename: str) -> FileResponse:
    path = resolve_generated_image(filename)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


__all__ = ["router", "resolve_generated_image"]
