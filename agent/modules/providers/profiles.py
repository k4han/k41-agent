"""Model capabilities shared by discovery and runtime invocation."""

from __future__ import annotations

from functools import lru_cache
from importlib import import_module
import json
import re
from typing import Any


EFFORT_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"


def parse_model_profiles(value: Any) -> dict[str, dict[str, Any]]:
    """Validate per-model reasoning metadata supplied through runtime settings."""
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        if not value.strip():
            return {}
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("Model profiles must be a JSON object.") from exc
    if not isinstance(value, dict):
        raise ValueError("Model profiles must be a JSON object keyed by model ID.")
    result = {}
    allowed = {"reasoning_effort_levels", "reasoning_effort_default"}
    for model_id, profile in value.items():
        if not isinstance(model_id, str) or not model_id.strip() or model_id != model_id.strip() or not isinstance(profile, dict):
            raise ValueError("Each model profile must have a non-empty model ID and an object value.")
        if set(profile) - allowed:
            raise ValueError(f"Unknown model profile fields for {model_id!r}.")
        levels = profile.get("reasoning_effort_levels")
        if "reasoning_effort_levels" in profile:
            if not isinstance(levels, list) or any(
                not isinstance(level, str) or not re.fullmatch(EFFORT_PATTERN, level)
                for level in levels
            ) or len(set(levels)) != len(levels):
                raise ValueError(f"Effort levels for {model_id!r} must be unique lowercase identifiers.")
        default = profile.get("reasoning_effort_default")
        if default is not None:
            if not isinstance(default, str) or not re.fullmatch(EFFORT_PATTERN, default):
                raise ValueError(f"Invalid default effort for {model_id!r}.")
            if levels is not None and default not in levels:
                raise ValueError(f"Default effort for {model_id!r} must be one of its levels.")
        result[model_id] = dict(profile)
    return result


@lru_cache(maxsize=2048)
def _langchain_profile(provider_type: str, model_name: str) -> dict[str, Any]:
    """Read the registry backing model.profile without constructing API clients.

    Partner packages currently expose these registries as private data modules.
    Keep that dependency here so a package layout change degrades to unknown
    metadata and can still be supplemented by provider settings.
    """
    package = {
        "openai": "langchain_openai",
        "openai_compatible": "langchain_openai",
        "anthropic": "langchain_anthropic",
        "google": "langchain_google_genai",
    }.get(provider_type)
    if package is None:
        return {}
    try:
        profiles = import_module(f"{package}.data._profiles")._PROFILES
    except (ImportError, AttributeError):
        return {}
    profile = profiles.get(model_name)
    if profile is None:
        prefix = {"langchain_openai": "openai/", "langchain_anthropic": "anthropic/",
                  "langchain_google_genai": "google/"}[package]
        if model_name.startswith(prefix):
            profile = profiles.get(model_name.removeprefix(prefix))
    return dict(profile or {})


def get_model_profile(
    provider_type: str,
    model_name: str,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge installed LangChain metadata with explicit model configuration."""
    profile = dict(_langchain_profile(provider_type, model_name))
    override = (overrides or {}).get(model_name, {})
    profile.update(override)
    if "reasoning_effort_levels" in override:
        profile["reasoning_output"] = bool(override["reasoning_effort_levels"])
        if "reasoning_effort_default" not in override:
            default = profile.get("reasoning_effort_default")
            if default not in override["reasoning_effort_levels"]:
                profile.pop("reasoning_effort_default", None)
    return profile


def reasoning_metadata(profile: dict[str, Any]) -> tuple[tuple[str, ...] | None, str | None]:
    """Distinguish missing effort metadata from explicitly unsupported models."""
    levels = profile.get("reasoning_effort_levels")
    if isinstance(levels, (list, tuple)):
        return tuple(levels), profile.get("reasoning_effort_default")
    if profile.get("reasoning_output") is False:
        return (), None
    return None, None
