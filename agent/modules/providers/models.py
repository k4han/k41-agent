"""Model configuration entities."""

from dataclasses import dataclass
import re
from typing import Any

from langchain_core.language_models import BaseChatModel


def supports_reasoning_effort(provider_type: str, model_name: str) -> bool:
    """Identify OpenAI reasoning models that accept all three effort levels."""
    if provider_type not in {"openai", "openai_compatible"}:
        return False
    name = model_name.rsplit("/", 1)[-1].lower()
    return bool(
        re.match(r"^(?:gpt-(?:5|6)(?:[.-]|$)|gpt-oss(?:-|$)|o[134](?:-|$))", name)
        and not re.search(r"(?:^|-)(?:pro|chat|search|preview|deep-research)(?:-|$)", name)
        and not name.startswith("o1-mini")
    )


def get_reasoning_effort_kwargs(
    provider_type: str, model_name: str, effort: str | None,
) -> dict[str, Any]:
    """Build request overrides without mutating the cached chat model."""
    if effort is None or not supports_reasoning_effort(provider_type, model_name):
        return {}
    if model_name.rsplit("/", 1)[-1].lower().startswith("gpt-6"):
        # GPT-6 tool calling requires the Responses API.
        return {"reasoning": {"effort": effort}}
    return {"reasoning_effort": effort}


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
    context_window: int | None = None
    input_types: tuple[str, ...] | None = None
    output_types: tuple[str, ...] | None = None


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
