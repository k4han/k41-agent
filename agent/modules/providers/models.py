"""Model configuration entities."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """Configuration for a specific model invocation."""

    model_name: str
    temperature: float = 0.0
    max_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ModelOption:
    """A selectable model exposed to API and dashboard clients."""

    id: str
    label: str
    source: str


@dataclass(frozen=True, slots=True)
class ProviderModelCatalog:
    """Model options available for a provider."""

    provider: str
    provider_type: str
    default_model: str
    can_list_models: bool
    models: tuple[ModelOption, ...]
    error: str | None = None
