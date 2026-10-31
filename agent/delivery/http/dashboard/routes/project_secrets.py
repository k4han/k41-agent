"""Write-only project secret API with sanitized validation errors."""

import logging

from fastapi import APIRouter, HTTPException, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

from agent.modules.projects import secrets
from agent.modules.tools import validate_secret_name, validate_secret_value

logger = logging.getLogger(__name__)


class SecretRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def sanitized(request: Request):
            try:
                response = await handler(request)
            except RequestValidationError as exc:
                location = next(iter(exc.errors()), {}).get("loc", ())
                field = location[-1] if location else None
                message = {
                    "name": "Secret names must be uppercase environment variable names of at most 255 characters and cannot use reserved runtime names.",
                    "value": "Secret values must contain 1 to 16384 UTF-8 bytes without null characters.",
                    "enabled": "The enabled field must contain a boolean value.",
                }.get(field, "Invalid project secret request.")
                raise HTTPException(422, message, headers={"Cache-Control": "no-store"}) from None
            except secrets.ProjectSecretError as exc:
                raise HTTPException(exc.status_code, str(exc), headers={"Cache-Control": "no-store"}) from None
            except Exception as exc:
                if isinstance(exc, HTTPException):
                    exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
                    raise
                # Database exceptions can contain bound parameter values.
                logger.warning("Project secret operation failed project_id=%s exception_type=%s",
                               request.path_params.get("project_id"), type(exc).__name__)
                raise HTTPException(503, "Project secrets are temporarily unavailable.",
                                    headers={"Cache-Control": "no-store"}) from None
            response.headers["Cache-Control"] = "no-store"
            return response

        return sanitized


router = APIRouter(route_class=SecretRoute)


class SecretCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str
    value: SecretStr
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def check_name(cls, value):
        return validate_secret_name(value)

    @field_validator("value")
    @classmethod
    def check_value(cls, value):
        validate_secret_value(value.get_secret_value())
        return value


class SecretUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    value: SecretStr | None = None
    enabled: bool | None = None

    @field_validator("value", "enabled")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Secret updates cannot contain null values.")
        if isinstance(value, SecretStr):
            validate_secret_value(value.get_secret_value())
        return value


@router.get("/dashboard-api/projects/{project_id}/secrets")
async def list_secrets(project_id: int = Path(ge=1)):
    return {"secrets": await secrets.list_project_secrets(project_id)}


@router.post("/dashboard-api/projects/{project_id}/secrets", status_code=201)
async def create_secret(body: SecretCreateBody, project_id: int = Path(ge=1)):
    return await secrets.create_project_secret(project_id, body.name, body.value.get_secret_value(), body.enabled)


@router.patch("/dashboard-api/projects/{project_id}/secrets/{name}")
async def update_secret(body: SecretUpdateBody, project_id: int = Path(ge=1), name: str = Path(pattern=r"^[A-Z_][A-Z0-9_]{0,254}$")):
    changes = body.model_dump(exclude_unset=True)
    if "value" in changes:
        changes["value"] = body.value.get_secret_value()
    try:
        return await secrets.update_project_secret(project_id, name, changes)
    except ValueError:
        raise HTTPException(422, "Invalid project secret request.") from None


@router.delete("/dashboard-api/projects/{project_id}/secrets/{name}", status_code=204)
async def delete_secret(project_id: int = Path(ge=1), name: str = Path(pattern=r"^[A-Z_][A-Z0-9_]{0,254}$")):
    await secrets.delete_project_secret(project_id, name)
    return Response(status_code=204)
