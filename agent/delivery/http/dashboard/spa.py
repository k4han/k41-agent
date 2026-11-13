from __future__ import annotations

import stat
from mimetypes import guess_type
from pathlib import Path

import anyio
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.staticfiles import NotModifiedResponse
from starlette.types import Scope

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
INDEX_FILE = STATIC_DIR / "index.html"

INDEX_CACHE_CONTROL = "no-cache, no-store, must-revalidate"
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"


def _encoding_qualities(value: str) -> dict[str, float]:
    qualities = {}
    for entry in value.lower().split(","):
        encoding, *parameters = entry.strip().split(";")
        quality = 1.0
        for parameter in parameters:
            name, _, weight = parameter.strip().partition("=")
            if name == "q":
                try:
                    quality = float(weight)
                except ValueError:
                    quality = 0.0
        if encoding:
            qualities[encoding.strip()] = quality if 0 <= quality <= 1 else 0.0
    return qualities


class CachedStaticFiles(StaticFiles):
    """Serve precompressed assets with representation-aware cache validation."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        # Validate the original path before considering adjacent compressed files.
        response = await super().get_response(path, scope)
        request_headers = Headers(scope=scope)
        qualities = _encoding_qualities(",".join(request_headers.getlist("accept-encoding")))
        identity_allowed = qualities.get("identity", 0 if qualities.get("*") == 0 else 1) > 0
        identity_quality = qualities.get("identity", 0)
        encodings = sorted(
            ((encoding, qualities.get(encoding, qualities.get("*", 0))) for encoding in ("br", "gzip")),
            key=lambda item: item[1],
            reverse=True,
        )
        compressed = False
        # Keep byte ranges on the original representation, including multipart ranges.
        if "range" not in request_headers:
            for encoding, quality in encodings:
                if quality <= 0 or quality < identity_quality:
                    continue
                suffix = ".br" if encoding == "br" else ".gz"
                full_path, stat_result = await anyio.to_thread.run_sync(self.lookup_path, path + suffix)
                if stat_result is None or not stat.S_ISREG(stat_result.st_mode):
                    continue
                response = FileResponse(
                    full_path,
                    stat_result=stat_result,
                    media_type=guess_type(path)[0] or "application/octet-stream",
                    headers={"Content-Encoding": encoding},
                )
                response.headers["etag"] = response.headers["etag"][:-1] + f'-{encoding}"'
                if self.is_not_modified(response.headers, request_headers):
                    response = NotModifiedResponse(response.headers)
                compressed = True
                break
        if not compressed and not identity_allowed:
            return Response(status_code=406, headers={"Vary": "Accept-Encoding"})
        response.headers.add_vary_header("Accept-Encoding")
        if path.startswith("assets/") or path.startswith("assets\\"):
            response.headers["Cache-Control"] = IMMUTABLE_CACHE_CONTROL
        elif path == "index.html":
            response.headers["Cache-Control"] = INDEX_CACHE_CONTROL
        return response


def spa_index_response() -> Response:
    if INDEX_FILE.is_file():
        return FileResponse(
            INDEX_FILE,
            media_type="text/html",
            headers={"Cache-Control": INDEX_CACHE_CONTROL},
        )

    return HTMLResponse(
        """<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link id="app-favicon" rel="icon" type="image/x-icon" href="/dashboard-assets/favicon.ico">
  <title>Kai</title>
</head>
<body>
  <div id="root">Dashboard frontend is not built. Run pnpm dashboard:build.</div>
</body>
</html>""",
        headers={"Cache-Control": INDEX_CACHE_CONTROL},
    )

