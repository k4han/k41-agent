"""Read and update the root instruction document of a catalog project."""

import base64

from agent.modules.projects.service import get_project
from agent.modules.skills import WorkspacePackageIO
from agent.modules.workspaces import WorkspaceUnavailableError
from agent.modules.workspaces import MAX_FILE_BYTES

INSTRUCTIONS_PATH = "AGENTS.md"


async def _operation(project_id: int, operation: str, **kwargs) -> dict:
    project = await get_project(project_id)
    transport = WorkspacePackageIO(project["workspace"]["execution"])
    try:
        return await transport.operation(
            operation, INSTRUCTIONS_PATH, require_existing_workspace=True,
            limits={"max_file_bytes": MAX_FILE_BYTES}, **kwargs,
        )
    except NotADirectoryError as exc:
        raise WorkspaceUnavailableError("Project workspace is unavailable.") from exc


def _document(content: str, version: str | None, *, exists: bool = True) -> dict:
    return {"path": INSTRUCTIONS_PATH, "exists": exists, "content": content, "version": version}


async def get_project_instructions(project_id: int) -> dict:
    try:
        result = await _operation(project_id, "read")
    except FileNotFoundError:
        return _document("", None, exists=False)
    raw = base64.b64decode(result["data"])
    content = raw.decode("utf-8-sig")
    if "\x00" in content:
        raise ValueError("AGENTS.md must be UTF-8 text without null bytes.")
    document = _document(content, result["version"])
    if "saved_revision" in result:
        document["saved_revision"] = result["saved_revision"]
        document["last_saved_at"] = result.get("last_saved_at")
    return document


async def save_project_instructions(project_id: int, content: str, expected_version: str | None) -> dict:
    raw = content.encode("utf-8")
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f"AGENTS.md must not exceed {MAX_FILE_BYTES} bytes.")
    if "\x00" in content:
        raise ValueError("AGENTS.md must be UTF-8 text without null bytes.")
    result = await _operation(
        project_id, "write", data=base64.b64encode(raw).decode(), expected_version=expected_version,
    )
    document = _document(content, result["version"])
    if "persistence" in result:
        document["persistence"] = result["persistence"]
    return document
