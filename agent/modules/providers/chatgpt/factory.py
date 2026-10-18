"""Create Responses clients whose credentials renew before every request."""

import asyncio

import httpx
from langchain_core.language_models import BaseChatModel
from openai import AsyncOpenAI, OpenAI

from agent.modules.providers.chatgpt.auth import ChatGPTAuthManager, RESOURCE
from agent.modules.providers.chatgpt.chat_model import ChatGPTChatModel
from agent.modules.providers.models import ModelConfig
from agent.modules.providers.provider import ProviderConfig


class ChatGPTFactory:
    def __init__(self, auth: ChatGPTAuthManager) -> None:
        self.auth = auth
        self.model_labels: dict[str, dict[str, str]] = {}

    def create(self, provider_config: ProviderConfig, model_config: ModelConfig, api_key: str) -> BaseChatModel:
        def token() -> str:
            return self.auth.access_token(provider_config.name)

        async def async_token() -> str:
            return await asyncio.to_thread(token)

        client = OpenAI(api_key=token, base_url=RESOURCE, max_retries=0)
        async_client = AsyncOpenAI(api_key=async_token, base_url=RESOURCE, max_retries=0)
        return ChatGPTChatModel(
            model=model_config.model_name, api_key="chatgpt-oauth",
            root_client=client, root_async_client=async_client,
            client=client.chat.completions, async_client=async_client.chat.completions,
            use_responses_api=True, streaming=True, store=False, max_retries=0,
            include=["reasoning.encrypted_content"], profile=model_config.profile or {},
        )

    async def list_models(self, provider_config: ProviderConfig, api_key: str, timeout: float = 10.0) -> list[str]:
        token = await asyncio.to_thread(self.auth.access_token, provider_config.name)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{RESOURCE}/models", headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            data = response.json()
        labels = {item["slug"]: item.get("display_name") or item["slug"]
                  for item in data.get("models", [])
                  if isinstance(item, dict) and item.get("visibility") == "list" and item.get("slug")}
        self.model_labels[provider_config.name] = labels
        return list(labels)
