"""Provider configuration entities."""

from dataclasses import dataclass
from enum import StrEnum


class ProviderType(StrEnum):
    """Supported LLM provider types."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OPENAI_COMPATIBLE = "openai_compatible"


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Configuration for a single LLM provider."""

    name: str
    provider_type: ProviderType
    base_url: str
    api_key: str
    default_model: str
    enabled: bool = True
