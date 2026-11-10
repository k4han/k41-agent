"""Instabox cloud machines: workspace runtime, previews, desktop and snapshots."""

from agent.modules.instabox.client import InstaboxClient, new_idempotency_key, redact
from agent.modules.instabox.config import (
    DEFAULT_INSTABOX_API_URL,
    DEFAULT_INSTABOX_ROOT,
    DEFAULT_INSTABOX_TEMPLATE,
    InstaboxSettings,
    load_instabox_settings,
)
from agent.modules.instabox.errors import (
    InstaboxApiError,
    InstaboxConfirmationError,
    InstaboxConflictError,
    InstaboxDisabledError,
    InstaboxError,
    is_instabox_auth_error,
    is_instabox_not_found,
    is_instabox_transient,
)
from agent.modules.instabox.models import InstaboxOperation, InstaboxPortToken
from agent.modules.instabox.service import InstaboxService, get_instabox_service, instabox_name
from agent.modules.instabox.states import machine_summary, normalize_machine_state, status_from_error


async def relay_vnc(*args, **kwargs):
    from agent.modules.instabox.vnc_proxy import relay_vnc as _relay_vnc

    return await _relay_vnc(*args, **kwargs)


__all__ = [
    "DEFAULT_INSTABOX_API_URL",
    "DEFAULT_INSTABOX_ROOT",
    "DEFAULT_INSTABOX_TEMPLATE",
    "InstaboxApiError",
    "InstaboxClient",
    "InstaboxConfirmationError",
    "InstaboxConflictError",
    "InstaboxDisabledError",
    "InstaboxError",
    "InstaboxOperation",
    "InstaboxPortToken",
    "InstaboxService",
    "InstaboxSettings",
    "get_instabox_service",
    "instabox_name",
    "is_instabox_auth_error",
    "is_instabox_not_found",
    "is_instabox_transient",
    "load_instabox_settings",
    "machine_summary",
    "new_idempotency_key",
    "normalize_machine_state",
    "redact",
    "relay_vnc",
    "status_from_error",
]
