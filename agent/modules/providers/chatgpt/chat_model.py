"""Enforce the stateless streaming contract for ChatGPT plan inference."""

from collections.abc import AsyncIterator, Iterator
from typing import Any

from langchain_core.language_models.chat_models import agenerate_from_stream, generate_from_stream
from langchain_core.outputs import ChatGenerationChunk, ChatResult
import openai

from agent.modules.providers.chatgpt.auth import USAGE_URL
from agent.modules.providers.chatgpt.streaming import ResponsesClient
from agent.modules.providers.openai_compatible.chat_model import OpenAICompatibleChatModel


_UNSUPPORTED_PARAMETERS = frozenset({
    "background", "conversation", "max_output_tokens", "max_tool_calls", "metadata",
    "moderation", "multi_agent", "prompt", "prompt_cache_retention", "safety_identifier",
    "temperature", "top_logprobs", "top_p", "truncation", "user",
})


class ChatGPTPlanUsageError(RuntimeError):
    """Preserve the machine-readable code from a failed Responses event."""

    def __init__(self, message: str, code: str):
        self.body = {"error": {"code": code}}
        super().__init__(message)


class ChatGPTChatModel(OpenAICompatibleChatModel):
    """Require a completed Responses stream for invoke and stream alike."""

    def _use_responses_api(self, payload: dict) -> bool:
        return True

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        yield from self._stream_responses(*args, **kwargs)

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        async for chunk in self._astream_responses(*args, **kwargs):
            yield chunk

    def _get_request_payload(self, *args: Any, **kwargs: Any) -> dict:
        payload = super()._get_request_payload(*args, **kwargs)
        if payload.get("previous_response_id"):
            raise ValueError("ChatGPT plan usage requires stateless conversation history.")
        payload.update(store=False, stream=True)
        extra_body = dict(payload.get("extra_body") or {})
        for key in _UNSUPPORTED_PARAMETERS:
            payload.pop(key, None)
            extra_body.pop(key, None)
        for key in ("store", "stream", "previous_response_id"):
            extra_body.pop(key, None)

        # The ChatGPT route accepts developer instructions, not system items.
        input_items = extra_body.pop("input", payload.get("input", []))
        if isinstance(input_items, str):
            input_items = [{"role": "user", "content": input_items}]
        payload["input"] = [
            {**item, "role": "developer"} if item.get("role") == "system" else item
            for item in input_items
        ]

        # additional_tools preserves unqualified names used by LangChain tools.
        tools = extra_body.pop("tools", payload.get("tools", []))
        local_tools = [tool for tool in tools if tool.get("type") in {"function", "custom"}]
        if local_tools:
            payload["input"].insert(0, {"type": "additional_tools", "role": "developer", "tools": local_tools})
        remaining_tools = [tool for tool in tools if tool.get("type") not in {"function", "custom"}]
        if remaining_tools:
            payload["tools"] = remaining_tools
        else:
            payload.pop("tools", None)

        if extra_body:
            payload["extra_body"] = extra_body
        else:
            payload.pop("extra_body", None)
        return payload

    @staticmethod
    def _check_chunk(chunk: ChatGenerationChunk) -> bool:
        status = chunk.message.response_metadata.get("status")
        if status == "incomplete":
            raise RuntimeError("ChatGPT response was incomplete. Please retry or adjust the request.")
        return status == "completed"

    @staticmethod
    def _inference_error(error: Exception) -> RuntimeError:
        for code in ("subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable"):
            if code in str(error):
                return ChatGPTPlanUsageError(f"ChatGPT plan usage is unavailable or has reached its limit. Manage usage at {USAGE_URL}.", code)
        return RuntimeError(str(error))

    def _stream_responses(self, *args: Any, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        completed = False
        request_model = self.model_copy(update={"root_client": ResponsesClient(self.root_client)})
        try:
            for chunk in super(ChatGPTChatModel, request_model)._stream_responses(*args, **kwargs):
                completed = self._check_chunk(chunk) or completed
                yield chunk
        except (ValueError, openai.APIStatusError) as exc:
            raise self._inference_error(exc) from exc
        if not completed:
            raise RuntimeError("ChatGPT stream ended without response.completed. Please retry.")

    async def _astream_responses(self, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        completed = False
        request_model = self.model_copy(update={
            "root_async_client": ResponsesClient(self.root_async_client, asynchronous=True),
        })
        try:
            async for chunk in super(ChatGPTChatModel, request_model)._astream_responses(*args, **kwargs):
                completed = self._check_chunk(chunk) or completed
                yield chunk
        except (ValueError, openai.APIStatusError) as exc:
            raise self._inference_error(exc) from exc
        if not completed:
            raise RuntimeError("ChatGPT stream ended without response.completed. Please retry.")

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        return generate_from_stream(self._stream(*args, **kwargs))

    async def _agenerate(self, *args: Any, **kwargs: Any) -> ChatResult:
        return await agenerate_from_stream(self._astream(*args, **kwargs))
