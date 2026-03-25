from agent.services.service_manager import ManagedService, ServiceManager, ServiceStatus


BotService = ManagedService
BotStatus = ServiceStatus


class BotManager(ServiceManager):
    """Backward-compatible singleton wrapper around ServiceManager."""

    _instance: "BotManager | None" = None

    @classmethod
    def get(cls) -> "BotManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
