"""Authenticated skill package management APIs."""

from __future__ import annotations

from dataclasses import asdict
import io
import json
import zipfile
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import yaml

from agent.modules.skills import get_skill_packages, validate_skill_document as validate_document

router = APIRouter(prefix="/dashboard-api/skill-packages")


def workspace_value(value: str | None):
    if not value:
        return None
    try:
        return json.loads(value) if value.startswith("{") else value
    except ValueError as exc:
        raise HTTPException(400, "Invalid workspace reference.") from exc


def package_error(exc: Exception):
    status = 404 if isinstance(exc, FileNotFoundError) else 409 if isinstance(exc, FileExistsError) else 403 if isinstance(exc, PermissionError) else 400 if isinstance(exc, (ValueError, UnicodeError, zipfile.BadZipFile)) else 503
    return HTTPException(status, str(exc))


class PackageBody(BaseModel):
    name: str
    content: str
    scope: Literal["global", "project"] = "global"
    workspace: Any = None


class ValidateBody(BaseModel):
    content: str
    name: str | None = None
    strict: bool = False


class UpdateBody(BaseModel):
    enabled: bool | None = None
    name: str | None = None
    expected_version: str | None = None


class DocumentBody(BaseModel):
    expected_version: str
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    body: str


class RenderBody(BaseModel):
    content: str
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    body: str


class EntryBody(BaseModel):
    action: Literal["mkdir", "rename", "delete"]
    path: str
    destination: str = ""
    expected_version: str | None = None


class ImportBody(BaseModel):
    preview_id: str
    scope: Literal["global", "project"] = "global"
    workspace: Any = None
    overwrite: bool = False


@router.get("")
async def list_packages(workspace: str | None = None, scope: Literal["global", "project"] | None = None):
    try:
        return {"packages": [asdict(item) for item in await get_skill_packages().inventory(workspace_value(workspace), scope)]}
    except Exception as exc:
        raise package_error(exc) from exc


@router.post("")
async def create_package(body: PackageBody):
    try:
        return await get_skill_packages().create(body.name, body.content, scope=body.scope, workspace=body.workspace)
    except Exception as exc:
        raise package_error(exc) from exc


@router.post("/validate")
async def validate_package(body: ValidateBody):
    return validate_document(body.content, name=body.name, strict=body.strict)


@router.post("/render")
async def render_document(body: RenderBody):
    document = validate_document(body.content)
    if not document["valid"]:
        raise HTTPException(400, document["diagnostics"][0]["message"])
    frontmatter = {**document["frontmatter"], **body.frontmatter}
    if body.frontmatter.get("compatibility") == "":
        frontmatter.pop("compatibility", None)
    return {"content": "---\n" + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True) + "---\n" + body.body + "\n"}


@router.post("/imports/preview")
async def preview_import(request: Request):
    try:
        if "application/json" in request.headers.get("content-type", ""):
            options = await request.json()
            data = None
        else:
            form = await request.form()
            options = json.loads(str(form.get("options", "{}")))
            upload = form.get("file")
            from agent.shared.config import get_config_service
            limit = get_config_service().get_int("skills.max_file_bytes", 64 * 1024 * 1024)
            data = await upload.read(limit + 1) if upload is not None else None
            if data is not None and len(data) > limit:
                raise ValueError("ZIP upload exceeds the limit.")
        if not isinstance(options, dict):
            raise ValueError("Import options must be an object.")
        if options.get("scope", "global") not in {"global", "project"}:
            raise ValueError("Invalid skill scope.")
        result = await get_skill_packages().preview(kind=options.get("kind", "zip"), path=options.get("path", ""), data=data,
            url=options.get("url", ""), ref=options.get("ref", "HEAD"), subdirectory=options.get("subdirectory", ""),
            installation_id=options.get("installation_id"), scope=options.get("scope", "global"), workspace=options.get("workspace"))
        existing = await get_skill_packages().inventory(options.get("workspace"), options.get("scope", "global"))
        names = {item.name for item in existing}
        for skill in result["skills"]:
            skill["conflict"] = skill["name"] in names
        return result
    except Exception as exc:
        raise package_error(exc) from exc


@router.post("/imports")
async def commit_import(body: ImportBody):
    try:
        return await get_skill_packages().import_preview(body.preview_id, scope=body.scope, workspace=body.workspace, overwrite=body.overwrite)
    except Exception as exc:
        raise package_error(exc) from exc


@router.get("/{identity}")
async def package_detail(identity: str, workspace: str | None = None):
    try:
        return {"package": await get_skill_packages().detail(identity, workspace_value(workspace))}
    except Exception as exc:
        raise package_error(exc) from exc


@router.patch("/{identity}")
async def update_package(identity: str, body: UpdateBody, workspace: str | None = None):
    try:
        packages = get_skill_packages()
        context = workspace_value(workspace)
        if body.enabled is not None:
            await packages.enabled(identity, body.enabled, context)
        if body.name is not None:
            if body.expected_version is None:
                raise ValueError("A document version is required when renaming.")
            await packages.rename(identity, body.name, expected_version=body.expected_version, workspace=context)
        return {"status": "updated"}
    except Exception as exc:
        raise package_error(exc) from exc


@router.delete("/{identity}")
async def delete_package(identity: str, workspace: str | None = None):
    try:
        await get_skill_packages().remove(identity, workspace_value(workspace))
        return {"status": "deleted"}
    except Exception as exc:
        raise package_error(exc) from exc


@router.put("/{identity}/document")
async def update_document(identity: str, body: DocumentBody, workspace: str | None = None):
    try:
        packages = get_skill_packages()
        context = workspace_value(workspace)
        detail = await packages.detail(identity, context)
        if detail["file_version"] != body.expected_version:
            raise FileExistsError("Skill document changed; reload before saving.")
        frontmatter = {**detail["frontmatter"], **body.frontmatter}
        if not frontmatter.get("name"):
            frontmatter["name"] = detail["name"]
        if body.frontmatter.get("compatibility") == "":
            frontmatter.pop("compatibility", None)
        content = "---\n" + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True) + "---\n" + body.body + "\n"
        return await packages.write_file(identity, "SKILL.md", content.encode(), expected_version=body.expected_version, workspace=context)
    except Exception as exc:
        raise package_error(exc) from exc


@router.get("/{identity}/files")
async def package_tree(identity: str, workspace: str | None = None, path: str = "", offset: int = 0, limit: int = 500):
    try:
        packages = get_skill_packages()
        package = await packages.find(identity, workspace_value(workspace))
        target = packages.resource_path(package, path) if path else package.source.directory
        result = await packages.transport(package).operation("tree", target, offset=offset, limit=limit)
        prefix = package.source.directory + "/"
        for entry in result["entries"]:
            entry["path"] = entry["path"].removeprefix(prefix)
        return result
    except Exception as exc:
        raise package_error(exc) from exc


@router.post("/{identity}/files")
async def mutate_entry(identity: str, body: EntryBody, workspace: str | None = None):
    try:
        packages = get_skill_packages()
        package = await packages.find(identity, workspace_value(workspace))
        result = await packages.transport(package).operation(body.action, packages.resource_path(package, body.path),
            destination=packages.resource_path(package, body.destination) if body.destination else "", expected_version=body.expected_version)
        packages.invalidate()
        return {"status": "updated", **result}
    except Exception as exc:
        raise package_error(exc) from exc


@router.get("/{identity}/files/{path:path}")
async def package_file(identity: str, path: str, workspace: str | None = None, download: bool = False):
    try:
        data, info = await get_skill_packages().read_file(identity, path, workspace_value(workspace))
        if download:
            return StreamingResponse(io.BytesIO(data), media_type=info["mime_type"], headers={"ETag": info["version"],
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(path.split('/')[-1])}"})
        try:
            text = data.decode("utf-8-sig") if b"\x00" not in data else None
        except UnicodeDecodeError:
            text = None
        return {**info, "path": path, "content": text if text is not None and len(data) <= 1024 * 1024 else None,
                "preview_truncated": text is not None and len(data) > 1024 * 1024}
    except Exception as exc:
        raise package_error(exc) from exc


@router.put("/{identity}/files/{path:path}")
async def put_package_file(identity: str, path: str, request: Request, workspace: str | None = None):
    try:
        from agent.shared.config import get_config_service
        limit = get_config_service().get_int("skills.max_file_bytes", 64 * 1024 * 1024)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError("File upload exceeds the limit.")
        return await get_skill_packages().write_file(identity, path, bytes(data), expected_version=request.headers.get("if-match"), workspace=workspace_value(workspace))
    except Exception as exc:
        raise package_error(exc) from exc


@router.get("/{identity}/export")
async def export_package(identity: str, workspace: str | None = None):
    try:
        packages = get_skill_packages()
        package = await packages.find(identity, workspace_value(workspace))
        data, version = await packages.transport(package).archive(package.source.directory)
        return StreamingResponse(io.BytesIO(data), media_type="application/zip", headers={"ETag": version,
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(package.name)}.zip"})
    except Exception as exc:
        raise package_error(exc) from exc
