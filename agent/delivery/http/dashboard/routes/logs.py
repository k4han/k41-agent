from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response

from agent.bootstrap.logs import LOG_BACKUP_COUNT, LOG_DIR, LOG_MAX_BYTES
from agent.shared.infrastructure.log_reader import (
    MAX_CHUNK_BYTES, LogHistoryExpired, LogSnapshot, LogSnapshotChanged, log_file_inventory,
)

router = APIRouter()
LOG_SOURCES = {"server": "Server", "tray": "System tray", "update": "Updater"}


def _read(source: str, operation):
    if source not in LOG_SOURCES:
        raise HTTPException(status_code=404, detail="Unknown log source.")
    for attempt in range(2):
        try:
            with LogSnapshot(LOG_DIR / f"{source}.log", LOG_BACKUP_COUNT) as snapshot:
                return operation(snapshot)
        except LogSnapshotChanged:
            if attempt:
                raise HTTPException(status_code=503, detail="Logs are rotating. Please retry.")
        except LogHistoryExpired:
            raise HTTPException(status_code=410, detail="This log history is no longer available. Return to the latest logs.")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=503, detail="Log files are currently unavailable.") from exc


@router.get("/dashboard-api/logs")
def get_log_sources(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    sources = []
    for source, label in LOG_SOURCES.items():
        try:
            inventory = log_file_inventory(LOG_DIR / f"{source}.log", LOG_BACKUP_COUNT)
            status = {
                "files": [{"name": path.name, "size": info.st_size} for path, info in inventory],
                "available": bool(inventory), "error": None,
            }
        except OSError:
            status = {"files": [], "available": False, "error": "Log files are currently unavailable."}
        sources.append({"id": source, "label": label, **status})
    return {"sources": sources, "max_bytes": LOG_MAX_BYTES, "backup_count": LOG_BACKUP_COUNT}


@router.get("/dashboard-api/logs/{source}")
def get_log_content(
    source: str, response: Response,
    before: str | None = Query(default=None, max_length=1024),
    after: str | None = Query(default=None, max_length=1024),
    limit: int = Query(default=MAX_CHUNK_BYTES, ge=4, le=MAX_CHUNK_BYTES),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    if before is not None and after is not None:
        raise HTTPException(status_code=400, detail="Use either before or after, not both.")
    if before == "" or after == "":
        raise HTTPException(status_code=400, detail="Invalid log cursor.")
    return _read(source, lambda snapshot: snapshot.page(source, before=before, after=after, limit=limit))
