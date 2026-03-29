import pytest
from pytest import MonkeyPatch

from agent.providers.llm import (
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    _resolve_llm_settings,
)


def clear_llm_env(monkeypatch: MonkeyPatch) -> None:
    for key in (
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_TEMPERATURE",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def test_resolve_llm_settings_prefers_llm_env(monkeypatch: MonkeyPatch) -> None:
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_MODEL", "custom-model")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.7")
    monkeypatch.setenv("OPENAI_API_KEY", "fallback-key")

    settings = _resolve_llm_settings()

    assert settings.api_key == "llm-key"
    assert settings.base_url == "https://example.test/v1"
    assert settings.model == "custom-model"
    assert settings.temperature == 0.7


def test_resolve_llm_settings_falls_back_to_openai_api_key(monkeypatch: MonkeyPatch) -> None:
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    settings = _resolve_llm_settings()

    assert settings.api_key == "openai-key"
    assert settings.base_url == DEFAULT_LLM_BASE_URL
    assert settings.model == DEFAULT_LLM_MODEL
    assert settings.temperature == 0.0


def test_resolve_llm_settings_requires_api_key(monkeypatch: MonkeyPatch) -> None:
    clear_llm_env(monkeypatch)

    with pytest.raises(RuntimeError, match="LLM_API_KEY or OPENAI_API_KEY"):
        _resolve_llm_settings()


def test_resolve_llm_settings_rejects_invalid_temperature(monkeypatch: MonkeyPatch) -> None:
    clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("LLM_TEMPERATURE", "not-a-number")

    with pytest.raises(ValueError, match="LLM_TEMPERATURE"):
        _resolve_llm_settings()
