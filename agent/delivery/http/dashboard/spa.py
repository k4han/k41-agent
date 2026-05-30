from __future__ import annotations

from pathlib import Path

from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
INDEX_FILE = STATIC_DIR / "index.html"

IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"


class CachedStaticFiles(StaticFiles):
    """StaticFiles that marks content-hashed build assets as immutable."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if path.startswith("assets/") or path.startswith("assets\\"):
            response.headers["Cache-Control"] = IMMUTABLE_CACHE_CONTROL
        return response


def spa_index_response() -> Response:
    if INDEX_FILE.is_file():
        return FileResponse(INDEX_FILE, media_type="text/html")

    return HTMLResponse(
        """<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Kaka</title>
</head>
<body>
  <div id="root">Dashboard frontend is not built. Run pnpm dashboard:build.</div>
</body>
</html>"""
    )

