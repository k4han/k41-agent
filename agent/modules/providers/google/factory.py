"""Factory for creating ChatGoogleGenerativeAI instances."""

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
        )

    async def list_models(
        self,
        provider_config: ProviderConfig,
        api_key: str,
    ) -> list[str]:
        try:
            import google.genai as genai
        except ImportError:
            return []

        _ = provider_config
        client = genai.Client(api_key=api_key)
        models = client.models.list()
        model_names: list[str] = []
        for model in models:
            name = str(getattr(model, "name", "")).strip()
            if name.startswith("models/"):
                name = name.removeprefix("models/")
            if name:
                model_names.append(name)
        return sorted(set(model_names))
