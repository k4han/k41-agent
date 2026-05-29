from __future__ import annotations

from pathlib import Path

from fastapi.responses import FileResponse, HTMLResponse, Response

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
INDEX_FILE = STATIC_DIR / "index.html"


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

