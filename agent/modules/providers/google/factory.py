"""Factory for creating ChatGoogleGenerativeAI instances."""

import asyncio

from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI

from agent.modules.providers.models import ModelConfig
from agent.modules.providers.provider import ProviderConfig


class GoogleFactory:
    """Create ChatGoogleGenerativeAI client from provider + model config."""

    def create(
        self, provider_config: ProviderConfig, model_config: ModelConfig, api_key: str
    ) -> BaseChatModel:
        _ = provider_config  # Google client does not use base_url.
        return ChatGoogleGenerativeAI(
            model=model_config.model_name,
            google_api_key=api_key,
            temperature=model_config.temperature,
            profile=model_config.profile,
        )

    async def list_models(
        self,
        provider_config: ProviderConfig,
        api_key: str,
        timeout: float = 10.0,
    ) -> list[str]:
        try:
            import google.genai as genai
        except ImportError as exc:
            raise RuntimeError(
                "google-genai package is not installed. Install it to enable Google model discovery."
            ) from exc

        timeout_ms = max(1000, int(timeout * 1000))

        def _list_sync() -> list[str]:
            client = genai.Client(api_key=api_key, http_options={"timeout": timeout_ms})
            try:
                models = client.models.list()
                model_names: list[str] = []
                for model in models:
                    name = str(getattr(model, "name", "")).strip()
                    if name.startswith("models/"):
                        name = name.removeprefix("models/")
                    if name:
                        model_names.append(name)
                return sorted(set(model_names))
            finally:
                try:
                    client.close()
                except Exception:
                    pass

        _ = provider_config
        return await asyncio.to_thread(_list_sync)
