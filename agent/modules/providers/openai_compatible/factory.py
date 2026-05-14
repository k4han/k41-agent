"""Factory for creating ChatOpenAI instances.

Covers OpenAI native, Mistral, and any OpenAI-compatible API.
"""

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from agent.modules.providers.models import ModelConfig
from agent.modules.providers.provider import ProviderConfig


class OpenAICompatibleFactory:
    """Create ChatOpenAI client from provider + model config."""

    def create(
        self, provider_config: ProviderConfig, model_config: ModelConfig, api_key: str
    ) -> BaseChatModel:
        kwargs = {
            "model": model_config.model_name,
            "api_key": api_key,
            "temperature": model_config.temperature,
        }
        if provider_config.base_url:
            kwargs["base_url"] = provider_config.base_url
        return ChatOpenAI(**kwargs)

    async def list_models(
        self,
        provider_config: ProviderConfig,
        api_key: str,
    ) -> list[str]:
        base_url = provider_config.base_url.rstrip("/") or "https://api.openai.com/v1"
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            data = response.json()

        raw_models = data.get("data", data if isinstance(data, list) else [])
        model_ids = []
        for item in raw_models:
            if isinstance(item, dict):
                model_id = str(item.get("id", "")).strip()
            else:
                model_id = str(item).strip()
            if model_id:
                model_ids.append(model_id)
        return sorted(set(model_ids))
