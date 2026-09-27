from agent.delivery.http.api import router as api_router
from agent.delivery.http.dashboard import router as dashboard_router
from agent.delivery.http.github_webhook import router as github_webhook_router
from agent.delivery.http.telegram_webhook import router as telegram_webhook_router
from agent.delivery.http.zalo_webhook import router as zalo_webhook_router
from agent.delivery.http.api.google_calendar import router as google_calendar_router

__all__ = [
    "api_router",
    "dashboard_router",
    "github_webhook_router",
    "telegram_webhook_router",
    "zalo_webhook_router",
    "google_calendar_router",
]
