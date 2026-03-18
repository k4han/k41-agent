# agent/adapters/fastapi/dashboard.py
# Dashboard API — bật/tắt/xem trạng thái các bot

from fastapi import APIRouter, HTTPException
from agent.services.bot_manager import BotManager

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/bots")
async def list_bots():
    """Xem trạng thái tất cả bots."""
    return {"bots": BotManager.get().status_all()}


@router.get("/bots/{name}")
async def get_bot(name: str):
    """Xem trạng thái 1 bot cụ thể."""
    try:
        return BotManager.get().status(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/bots/{name}/start")
async def start_bot(name: str):
    """Bật 1 bot."""
    try:
        await BotManager.get().start(name)
        return {"message": f"'{name}' đang khởi động.", **BotManager.get().status(name)}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bots/{name}/stop")
async def stop_bot(name: str):
    """Tắt 1 bot."""
    try:
        await BotManager.get().stop(name)
        return {"message": f"'{name}' đã dừng.", **BotManager.get().status(name)}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bots/start-all")
async def start_all():
    """Bật tất cả bots."""
    await BotManager.get().start_all()
    return {"bots": BotManager.get().status_all()}


@router.post("/bots/stop-all")
async def stop_all():
    """Tắt tất cả bots."""
    await BotManager.get().stop_all()
    return {"bots": BotManager.get().status_all()}
