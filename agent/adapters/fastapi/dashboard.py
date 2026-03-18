# agent/adapters/fastapi/dashboard.py
# Dashboard API — enable/disable/view status of bots

from fastapi import APIRouter, HTTPException
from agent.services.bot_manager import BotManager

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/bots")
async def list_bots():
    """View status of all bots."""
    return {"bots": BotManager.get().status_all()}


@router.get("/bots/{name}")
async def get_bot(name: str):
    """View status of a specific bot."""
    try:
        return BotManager.get().status(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/bots/{name}/start")
async def start_bot(name: str):
    """Start a bot."""
    try:
        await BotManager.get().start(name)
        return {"message": f"'{name}' is starting.", **BotManager.get().status(name)}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bots/{name}/stop")
async def stop_bot(name: str):
    """Stop a bot."""
    try:
        await BotManager.get().stop(name)
        return {"message": f"'{name}' stopped.", **BotManager.get().status(name)}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bots/start-all")
async def start_all():
    """Start all bots."""
    await BotManager.get().start_all()
    return {"bots": BotManager.get().status_all()}


@router.post("/bots/stop-all")
async def stop_all():
    """Stop all bots."""
    await BotManager.get().stop_all()
    return {"bots": BotManager.get().status_all()}
