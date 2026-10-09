"""Reserve response capacity and estimation headroom for model inputs."""

from typing import Any

from agent.modules.providers.context_window import DEFAULT_CONTEXT_WINDOW


def model_input_budget(resolved: Any, threshold: int = 100) -> int:
    """Return an input ceiling bounded by the compaction target and capacity.

    Explicit output limits take precedence over a modest default reservation.
    Profile output capacity is not a requested response size.
    """
    window = getattr(resolved, "context_window", DEFAULT_CONTEXT_WINDOW)
    model = getattr(resolved, "model", None)
    output_limits = [
        getattr(model, name, None)
        for name in ("max_tokens", "max_completion_tokens", "max_output_tokens")
    ]
    model_kwargs = getattr(model, "model_kwargs", None)
    if isinstance(model_kwargs, dict):
        output_limits.extend(model_kwargs.get(name) for name in (
            "max_tokens", "max_completion_tokens", "max_output_tokens",
        ))
    reserve = next(
        (value for value in output_limits if type(value) is int and value > 0),
        min(4096, max(1, window // 8)),
    )
    headroom = max(1, min(2048, window // 50))
    return max(0, min(window * threshold // 100, window - reserve - headroom))
