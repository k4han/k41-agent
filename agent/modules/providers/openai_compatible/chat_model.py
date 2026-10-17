"""Normalize cumulative usage snapshots from OpenAI-compatible gateways."""

from collections.abc import AsyncIterator, Iterator
from typing import Any

from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI


class OpenAICompatibleChatModel(ChatOpenAI):
    """Count the final Chat Completions usage snapshot once per request.

    Some gateways attach usage to both the finish chunk and a trailing chunk
    with no choices. These are request totals, including corrected totals,
    while LangChain adds chunk usage together. Stream content immediately
    and publish only the last usage snapshot when the request completes.
    Responses API events retain their native streaming behavior.
    """

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
