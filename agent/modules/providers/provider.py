"""Provider configuration entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


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
    models: tuple[str, ...] = ()
    enabled: bool = True
    extra_body: dict[str, Any] | None = field(default=None)
    catalog_id: str = ""
    model_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
