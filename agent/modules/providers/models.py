"""Model configuration entities."""

from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel

from agent.modules.providers.profiles import get_model_profile, reasoning_metadata


def supports_reasoning_effort(provider_type: str, model_name: str) -> bool:
    """Identify models with declared configurable reasoning effort."""
    levels, _ = reasoning_metadata(get_model_profile(provider_type, model_name))
    return bool(levels)


def get_reasoning_effort_kwargs(
    provider_type: str, model_name: str, effort: str | None,
    *, profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build request overrides without mutating the cached chat model."""
    levels, default = reasoning_metadata(
        profile if profile is not None else get_model_profile(provider_type, model_name)
    )
    if not levels:
        return {}
    if effort is None:
        effort = default if default in levels else None
    if effort is None:
        return {}
    if effort not in levels:
        raise ValueError(f"Unsupported reasoning effort {effort!r} for model {model_name!r}. Allowed: {', '.join(levels)}.")
    if provider_type in {"openai", "openai_compatible"} and model_name.rsplit("/", 1)[-1].lower().startswith("gpt-6"):
        # Preserve Responses API routing for GPT-6 tool calls.
        return {"reasoning": {"effort": effort}}
    return {"reasoning_effort": effort}


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """Configuration for a specific model invocation."""

    model_name: str
    temperature: float = 0.0
    max_tokens: int | None = None
    profile: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ModelOption:
    """A selectable model exposed to API and dashboard clients."""

    id: str
    label: str
    source: str
    context_window: int | None = None
    input_types: tuple[str, ...] | None = None
    output_types: tuple[str, ...] | None = None
    reasoning_effort_levels: tuple[str, ...] | None = None
    reasoning_effort_default: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderModelCatalog:
    """Model options available for a provider."""

    provider: str
    provider_type: str
    default_model: str
    can_list_models: bool
    models: tuple[ModelOption, ...]
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedChatModel:
    """Chat model instance with the resolved provider and model identity."""

    model: BaseChatModel
    provider_name: str
    provider_type: str
    model_name: str
    profile: dict[str, Any] | None = None
    used_fallback: bool = False
