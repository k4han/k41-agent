"""Public facade for the admin_auth module."""

from __future__ import annotations

from agent.modules.admin_auth.application.auth import (
    create_access_token,
    get_current_admin,
    get_secret_key,
)
from agent.modules.admin_auth.application.service import (
    AdminAuthService,
    get_admin_auth_service,
)
from agent.modules.admin_auth.infrastructure.models import AdminCredential


__all__ = [
    "AdminAuthService",
    "AdminCredential",
    "create_access_token",
    "get_admin_auth_service",
    "get_current_admin",
    "get_secret_key",
]