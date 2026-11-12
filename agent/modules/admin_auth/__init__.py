"""Public facade for the admin_auth module."""

from __future__ import annotations

from agent.modules.admin_auth.auth import (
    create_access_token,
    get_current_admin,
    get_secret_key,
)
from agent.modules.admin_auth.service import (
    AdminAuthService,
    DEFAULT_ADMIN_PASSWORD,
    LoginThrottled,
    get_admin_auth_service,
)
from agent.modules.admin_auth.models import AdminCredential
from agent.modules.admin_auth.migrations import migrate_admin_auth_tables


__all__ = [
    "AdminAuthService",
    "AdminCredential",
    "DEFAULT_ADMIN_PASSWORD",
    "LoginThrottled",
    "migrate_admin_auth_tables",
    "create_access_token",
    "get_admin_auth_service",
    "get_current_admin",
    "get_secret_key",
]
