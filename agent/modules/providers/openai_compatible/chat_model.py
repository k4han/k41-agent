"""Normalize cumulative usage snapshots from OpenAI-compatible gateways."""

from collections.abc import AsyncIterator, Iterator
from typing import Any

import openai
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
from openai.types.completion_usage import PromptTokensDetails


def _normalize_cache_usage(usage: dict | None) -> dict | None:
    """Map DeepSeek's cache-hit alias before LangChain discards custom usage fields."""
    if not isinstance(usage, dict):
        return usage
    details = usage.get("prompt_tokens_details") or {}
    if details.get("cached_tokens") is not None or usage.get("prompt_cache_hit_tokens") is None:
        return usage
    return {**usage, "prompt_tokens_details": {**details, "cached_tokens": usage["prompt_cache_hit_tokens"]}}


class OpenAICompatibleChatModel(ChatOpenAI):
    """Count the final Chat Completions usage snapshot once per request.

    Some gateways attach usage to both the finish chunk and a trailing chunk
    with no choices. These are request totals, including corrected totals,
    while LangChain adds chunk usage together. Stream content immediately
    and publish only the last usage snapshot when the request completes.
    Responses API events retain their native streaming behavior.
    """

    def _convert_chunk_to_generation_chunk(
        self, chunk: dict, default_chunk_class: type, base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        usage = _normalize_cache_usage(chunk.get("usage"))
        if usage is not chunk.get("usage"):
            chunk = {**chunk, "usage": usage}
        return super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)

    def _create_chat_result(
        self, response: dict | openai.BaseModel, generation_info: dict | None = None,
    ) -> ChatResult:
        if isinstance(response, dict):
            usage = _normalize_cache_usage(response.get("usage"))
            if usage is not response.get("usage"):
                response = {**response, "usage": usage}
        elif isinstance(response, openai.BaseModel) and getattr(response, "usage", None) is not None:
            original_usage = response.usage.model_dump()
            usage = _normalize_cache_usage(original_usage)
            if usage is not original_usage:
                response = response.model_copy(update={"usage": response.usage.model_copy(update={
                    "prompt_tokens_details": PromptTokensDetails(**usage["prompt_tokens_details"]),
                })})
        return super()._create_chat_result(response, generation_info)

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        if self._use_responses_api({**kwargs, **self.model_kwargs}):
            yield from super()._stream(*args, **kwargs)
            return

        last_usage = None
        for chunk in super()._stream(*args, **kwargs):
            if isinstance(chunk.message, AIMessageChunk) and chunk.message.usage_metadata is not None:
                last_usage = chunk.message.usage_metadata
                chunk = chunk.model_copy(update={
                    "message": chunk.message.model_copy(update={"usage_metadata": None}),
                })
            yield chunk
        if last_usage is not None:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", usage_metadata=last_usage))

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        if self._use_responses_api({**kwargs, **self.model_kwargs}):
            async for chunk in super()._astream(*args, **kwargs):
                yield chunk
            return

        last_usage = None
        async for chunk in super()._astream(*args, **kwargs):
            if isinstance(chunk.message, AIMessageChunk) and chunk.message.usage_metadata is not None:
                last_usage = chunk.message.usage_metadata
                chunk = chunk.model_copy(update={
                    "message": chunk.message.model_copy(update={"usage_metadata": None}),
                })
            yield chunk
        if last_usage is not None:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", usage_metadata=last_usage))
