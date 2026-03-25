from fastapi import APIRouter, HTTPException, Request

from agent.services import ServiceManager

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _get_service_manager(request: Request) -> ServiceManager:
    service_manager = getattr(request.app.state, "service_manager", None)
    if service_manager is None:
        raise HTTPException(status_code=503, detail="Service manager is not available.")
    return service_manager


def _collection_payload(service_manager: ServiceManager) -> dict[str, list[dict[str, str | None]]]:
    services = service_manager.status_all()
    return {"services": services}


@router.get("/services")
@router.get("/bots", include_in_schema=False)
async def list_services(request: Request):
    """View status of all managed services."""
    service_manager = _get_service_manager(request)
    return _collection_payload(service_manager)


@router.get("/services/{name}")
@router.get("/bots/{name}", include_in_schema=False)
async def get_service(name: str, request: Request):
    """View status of a specific service."""
    service_manager = _get_service_manager(request)
    try:
        return service_manager.status(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/services/{name}/start")
@router.post("/bots/{name}/start", include_in_schema=False)
async def start_service(name: str, request: Request):
    """Start a service."""
    service_manager = _get_service_manager(request)
    try:
        await service_manager.start(name)
        return {"message": f"'{name}' is starting.", **service_manager.status(name)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/services/{name}/stop")
@router.post("/bots/{name}/stop", include_in_schema=False)
async def stop_service(name: str, request: Request):
    """Stop a service."""
    service_manager = _get_service_manager(request)
    try:
        await service_manager.stop(name)
        return {"message": f"'{name}' stopped.", **service_manager.status(name)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/services/start-all")
@router.post("/bots/start-all", include_in_schema=False)
async def start_all_services(request: Request):
    """Start all registered services."""
    service_manager = _get_service_manager(request)
    await service_manager.start_all()
    return _collection_payload(service_manager)


@router.post("/services/stop-all")
@router.post("/bots/stop-all", include_in_schema=False)
async def stop_all_services(request: Request):
    """Stop all registered services."""
    service_manager = _get_service_manager(request)
    await service_manager.stop_all()
    return _collection_payload(service_manager)
