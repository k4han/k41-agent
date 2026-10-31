"""Project-scoped credential management and execution-time resolution."""

from __future__ import annotations

import logging
import posixpath

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agent.modules.projects import service as catalog
from agent.modules.projects.models import Project, ProjectSecret
from agent.modules.tools import validate_secret_name, validate_secret_value
from agent.shared.infrastructure.db.base import utcnow
from agent.shared.infrastructure.secret_cipher import SecretCipher, default_key_path

logger = logging.getLogger(__name__)


class ProjectSecretError(Exception):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


def get_secret_cipher() -> SecretCipher:
    return SecretCipher(default_key_path())


def _metadata(row: ProjectSecret) -> dict:
    from agent.modules.projects import storage_timestamp
    return {"name": row.name, "enabled": row.enabled,
            "created_at": storage_timestamp(row.created_at), "updated_at": storage_timestamp(row.updated_at)}


async def _require_project(session, project_id: int) -> None:
    if await session.get(Project, project_id) is None:
        raise ProjectSecretError("Project not found.", 404)


async def list_project_secrets(project_id: int) -> list[dict]:
    session = await catalog.get_async_session()
    async with session:
        await _require_project(session, project_id)
        rows = (await session.execute(select(ProjectSecret).where(
            ProjectSecret.project_id == project_id).order_by(ProjectSecret.name))).scalars()
        return [_metadata(row) for row in rows]


async def _encrypt(session, value: str) -> str:
    cipher = get_secret_cipher()
    try:
        if not cipher.key_path.exists() and (await session.execute(select(ProjectSecret.id).limit(1))).first():
            raise FileNotFoundError
        return cipher.encrypt(value)
    except (OSError, ValueError):
        raise ProjectSecretError("Project secret encryption is unavailable. Check the server encryption key.") from None


async def create_project_secret(project_id: int, name: str, value: str, enabled: bool = True) -> dict:
    validate_secret_name(name)
    validate_secret_value(value)
    session = await catalog.get_async_session()
    async with session:
        await _require_project(session, project_id)
        row = ProjectSecret(project_id=project_id, name=name, enabled=enabled,
                            encrypted_value=await _encrypt(session, value))
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise ProjectSecretError("A secret with this name already exists in the project.", 409) from None
        result = _metadata(row)
    logger.info("Project secret created project_id=%s name=%s", project_id, name)
    return result


async def update_project_secret(project_id: int, name: str, changes: dict) -> dict:
    validate_secret_name(name)
    if "value" in changes:
        validate_secret_value(changes["value"])
    session = await catalog.get_async_session()
    async with session:
        await _require_project(session, project_id)
        row = (await session.execute(select(ProjectSecret).where(
            ProjectSecret.project_id == project_id, ProjectSecret.name == name))).scalar_one_or_none()
        if row is None:
            raise ProjectSecretError("Project secret not found.", 404)
        if "value" in changes:
            row.encrypted_value = await _encrypt(session, changes["value"])
        if "enabled" in changes:
            row.enabled = changes["enabled"]
        row.updated_at = utcnow()
        await session.commit()
        result = _metadata(row)
    logger.info("Project secret updated project_id=%s name=%s", project_id, name)
    return result


async def delete_project_secret(project_id: int, name: str) -> None:
    session = await catalog.get_async_session()
    async with session:
        await _require_project(session, project_id)
        row = (await session.execute(select(ProjectSecret).where(
            ProjectSecret.project_id == project_id, ProjectSecret.name == name))).scalar_one_or_none()
        if row is None:
            raise ProjectSecretError("Project secret not found.", 404)
        await session.delete(row)
        await session.commit()
    logger.info("Project secret deleted project_id=%s name=%s", project_id, name)


async def resolve_execution_secrets(context) -> dict[str, str]:
    """Resolve only after authorization, against the current durable binding."""
    from agent.modules.projects import get_project_workspace_service
    from agent.modules.tools import CodingError
    if not context.project_id and not context.project_workspace_id:
        return {}
    if not context.project_id or not context.project_workspace_id or not context.runtime_generation:
        raise CodingError("stale_runtime", "Project secrets require a current project workspace binding.")
    binding = await get_project_workspace_service().binding(context.project_id, context.project_workspace_id)
    if (binding.runtime_generation != context.runtime_generation or binding.execution.backend != context.backend
            or binding.execution.locator != context.locator):
        raise CodingError("stale_runtime", "Project execution runtime changed before resolving secrets.")
    expected_root = binding.execution.locator if context.backend == "local" else str(binding.execution.metadata.get("root", ""))
    from agent.modules.tools import comparable_path
    roots_match = (comparable_path(expected_root) == comparable_path(context.workspace) if context.backend == "local"
                   else posixpath.normpath(expected_root) == posixpath.normpath(context.workspace))
    if not roots_match:
        raise CodingError("stale_runtime", "Project workspace root changed before resolving secrets.")
    try:
        session = await catalog.get_async_session()
        async with session:
            rows = list((await session.execute(select(ProjectSecret).where(
                ProjectSecret.project_id == binding.project_id, ProjectSecret.enabled.is_(True)))).scalars())
        if not rows:
            return {}
        cipher = get_secret_cipher()
        return {row.name: cipher.decrypt(row.encrypted_value) for row in rows}
    except Exception:
        raise CodingError("secret_unavailable", "Project secrets could not be loaded. Check the server database and encryption key.") from None


async def project_secret_context(workspace, *, thread_id: str = "") -> str:
    """Return fresh names only; never decrypt values while building prompts."""
    from agent.bootstrap.container import get_active_container
    container = get_active_container()
    if container is None or not container._persistence_ready:
        return ""
    from agent.modules.projects import get_project_workspace_service
    from agent.modules.projects.workspaces import ProjectWorkspaceError
    try:
        binding = await get_project_workspace_service().find_binding(workspace, thread_id=thread_id or None)
        if binding is None:
            return ""
        names = [item["name"] for item in await list_project_secrets(binding.project_id) if item["enabled"]]
    except (ProjectWorkspaceError, ProjectSecretError, KeyError, SQLAlchemyError) as exc:
        logger.warning("Project secret prompt context unavailable (%s).", getattr(exc, "code", type(exc).__name__))
        return ""
    if not names:
        return ""
    return ("\n\n<project_secrets>\nThe following environment variables are provided automatically to commands "
            "started in this project: " + ", ".join(names) + ". Use them by name. Do not print, persist, "
            "or include their values in commands or messages.\n</project_secrets>")
