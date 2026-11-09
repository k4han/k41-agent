"""Factory for creating ChatGoogleGenerativeAI instances."""

import asyncio

from langchain_core.language_models import BaseChatModel

from agent.modules.providers.google.chat_model import GoogleChatModel
from agent.modules.providers.models import ModelConfig
from agent.modules.providers.provider import ProviderConfig


def _returns_thoughts(model_name: str) -> bool:
    """Whether Gemini should include thought summaries in its replies.

    Thinking models only send thoughts on request. Gemini 1.x and 2.0 do not
    think, and Gemma already returns its reasoning without the flag.
    """
    name = model_name.rsplit("/", 1)[-1].lower()
    return name.startswith("gemini-") and not name.startswith(("gemini-1", "gemini-2.0"))


class GoogleFactory:
    """Create ChatGoogleGenerativeAI client from provider + model config."""

    def create(
        self, provider_config: ProviderConfig, model_config: ModelConfig, api_key: str
    ) -> BaseChatModel:
        _ = provider_config  # Google client does not use base_url.
        return GoogleChatModel(
            model=model_config.model_name,
            google_api_key=api_key,
            temperature=model_config.temperature,
            profile=model_config.profile,
            # Leave the flag unset elsewhere so no thinking config is sent at all.
            **({"include_thoughts": True} if _returns_thoughts(model_config.model_name) else {}),
            # Google expresses this value as total attempts, including the first call.
            **({"max_retries": 1} if model_config.disable_retries else {}),
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
