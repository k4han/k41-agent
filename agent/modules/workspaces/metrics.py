"""Low-cost diagnostics for execution activity and provider overhead."""

from dataclasses import asdict, dataclass


@dataclass
class WorkspaceRuntimeMetrics:
    rpc_count: int = 0
    monitor_rpc_count: int = 0
    checkpoint_count: int = 0
    checkpoint_seconds: float = 0.0
    provision_count: int = 0
    recovery_count: int = 0
    last_user_activity: str | None = None
    last_monitor_activity: str | None = None

    def snapshot(self):
        return asdict(self)
