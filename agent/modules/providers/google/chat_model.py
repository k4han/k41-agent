"""Adapt Google requests when replaying history across model families."""

from typing import Any

from langchain_core.messages import BaseMessage
from langchain_google_genai import ChatGoogleGenerativeAI


class GoogleChatModel(ChatGoogleGenerativeAI):
    """Keep Gemini thought signatures out of requests to Gemma."""

    def _prepare_request(self, messages: list[BaseMessage], **kwargs: Any) -> dict[str, Any]:
        request = super()._prepare_request(messages, **kwargs)
        if self.model.rsplit("/", 1)[-1].lower().startswith("gemma-"):
            # Gemma rejects Gemini signatures carried by replayed tool calls or text.
            # Modify the converted request so persisted messages remain intact.
            for content in request["contents"]:
                for part in content.parts or []:
                    part.thought_signature = None
        return request
