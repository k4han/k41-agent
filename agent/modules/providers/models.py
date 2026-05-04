"""Model configuration entities."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """Configuration for a specific model invocation."""

    model_name: str
    temperature: float = 0.0
    max_tokens: int | None = None
