from __future__ import annotations

from fastapi import HTTPException, Request

from agent.modules.channels import ChannelManager
from agent.shared.config import ConfigService


def get_app_container(request: Request):
    """Resolve AppContainer from request state or active scope."""
    from agent.bootstrap.container import get_container

    try:
        return get_container(request)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="AppContainer is not available.") from exc


def get_channel_manager(request: Request) -> ChannelManager:
    state = getattr(request.app, "state", None)
    channel_manager = getattr(state, "channel_manager", None) if state else None
    if channel_manager is None:
        try:
            container = get_app_container(request)
            channel_manager = container.channel_manager
        except HTTPException:
            channel_manager = None
    if channel_manager is None:
        raise HTTPException(status_code=503, detail="Channel manager is not available.")
    return channel_manager


def get_request_config_service(request: Request) -> ConfigService:
    state = getattr(request.app, "state", None)
    service = getattr(state, "config_service", None) if state else None
    if service is None:
        try:
            container = get_app_container(request)
            service = container.config_service
        except HTTPException:
            service = None
    if service is None:
        raise HTTPException(status_code=503, detail="Config service is not available.")
    return service
