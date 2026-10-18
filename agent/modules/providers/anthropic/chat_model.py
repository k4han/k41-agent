"""Preserve cache usage from Anthropic's initial and cumulative stream events."""

from collections.abc import AsyncIterator, Iterator
from typing import Any

from anthropic.types import Usage
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from pydantic import PrivateAttr


class AnthropicChatModel(ChatAnthropic):
    """Merge raw usage snapshots and report the completed request exactly once."""

    _usage_snapshot: dict[str, Any] = PrivateAttr(default_factory=dict)

    def _make_message_chunk_from_anthropic_event(
        self, event: Any, **kwargs: Any,
    ) -> tuple[AIMessageChunk | None, Any]:
        if event.type == "message_start":
            self._usage_snapshot = event.message.usage.model_dump(exclude_none=True)
        elif event.type == "message_delta":
            incoming = event.usage.model_dump(exclude_none=True)
            if ("cache_creation_input_tokens" in incoming and "cache_creation" not in incoming
                and incoming["cache_creation_input_tokens"] != self._usage_snapshot.get("cache_creation_input_tokens")):
                self._usage_snapshot.pop("cache_creation", None)
            self._usage_snapshot = {**self._usage_snapshot, **incoming}
            usage = Usage.model_validate({"input_tokens": 0, **self._usage_snapshot})
            event = event.model_copy(update={"usage": usage})
        return super()._make_message_chunk_from_anthropic_event(event, **kwargs)

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        # Keep snapshot state local to this request while sharing the SDK clients.
        request_model = self.model_copy()
        request_model._usage_snapshot = {}
        last_usage = None
        for chunk in super(AnthropicChatModel, request_model)._stream(*args, **kwargs):
            if isinstance(chunk.message, AIMessageChunk) and chunk.message.usage_metadata is not None:
                last_usage = chunk.message.usage_metadata
                chunk = chunk.model_copy(update={
                    "message": chunk.message.model_copy(update={"usage_metadata": None}),
                })
            yield chunk
        if last_usage is not None:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", usage_metadata=last_usage))

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        request_model = self.model_copy()
        request_model._usage_snapshot = {}
        last_usage = None
        async for chunk in super(AnthropicChatModel, request_model)._astream(*args, **kwargs):
            if isinstance(chunk.message, AIMessageChunk) and chunk.message.usage_metadata is not None:
                last_usage = chunk.message.usage_metadata
                chunk = chunk.model_copy(update={
                    "message": chunk.message.model_copy(update={"usage_metadata": None}),
                })
            yield chunk
        if last_usage is not None:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", usage_metadata=last_usage))
