"""Runtime and environment configuration for the decisions module."""

from __future__ import annotations

from enum import Enum
from typing import Any, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from agent.shared.config import ConfigService


class RouterMode(str, Enum):
    """Execution mode for agent routing strategy."""

    LLM_ONLY = "llm_only"
    CLEF_ONLY = "clef_only"
    SHADOW = "shadow"
    CASCADE = "cascade"


class DecisionSettings(BaseModel):
    """Strongly-typed settings for decision service and client operations."""

    model_config = ConfigDict(extra="ignore")

    provider_type: str = "cloudflare"
    provider_name: str = ""
    provider_fields: dict[str, Any] = Field(default_factory=dict)
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""
    cloudflare_base_url: str = "https://api.cloudflare.com/client/v4"
    model: str = "@cf/cloudflare/clef-flash"
    timeout: float = 5.0
    max_retries: int = 2
    router_mode: RouterMode = RouterMode.CASCADE
    router_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    router_log_telemetry: bool = True


def load_decision_settings(config: ConfigService | None = None) -> DecisionSettings:
    """Load DecisionSettings from ConfigService with environment variable fallback.

    Falls back to CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN environment variables
    if configuration values are blank or unset.
    """
    if config is None:
        from agent.shared.config import get_config_service

        config = get_config_service()

    from agent.shared.config.decision_providers import resolve_provider
    provider = resolve_provider(config)
    account_id = str(provider.get("account_id", ""))
    api_token = str(provider.get("api_token", ""))
    base_url = str(provider.get("base_url", "https://api.cloudflare.com/client/v4"))
    model = str(provider.get("model", "@cf/cloudflare/clef-flash"))
    try:
        timeout = float(provider.get("timeout", 5.0))
    except (ValueError, TypeError):
        timeout = 5.0
    try:
        max_retries = int(provider.get("max_retries", 2))
    except (ValueError, TypeError):
        max_retries = 2

    mode_str = config.get_str("decision.router.mode", "cascade").strip().lower()
    try:
        router_mode = RouterMode(mode_str)
    except ValueError:
        router_mode = RouterMode.CASCADE

    threshold_raw = config.get("decision.router.threshold", 0.75)
    try:
        router_threshold = float(threshold_raw) if threshold_raw is not None else 0.75
        router_threshold = max(0.0, min(1.0, router_threshold))
    except (ValueError, TypeError):
        router_threshold = 0.75

    router_log_telemetry = config.get_bool("decision.router.log_telemetry", True)

    return DecisionSettings(
        provider_type=provider["type"],
        provider_name=provider["name"],
        provider_fields=provider,
        cloudflare_account_id=account_id,
        cloudflare_api_token=api_token,
        cloudflare_base_url=base_url,
        model=model,
        timeout=timeout,
        max_retries=max_retries,
        router_mode=router_mode,
        router_threshold=router_threshold,
        router_log_telemetry=router_log_telemetry,
    )
