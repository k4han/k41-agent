"""Adapt Google requests when replaying history across model families."""

from typing import Any, cast

from langchain_core.messages import BaseMessage
from langchain_google_genai import ChatGoogleGenerativeAI


class GoogleChatModel(ChatGoogleGenerativeAI):
    """Preserve replay compatibility for Gemma and Gemini model aliases."""

    def _prepare_request(self, messages: list[BaseMessage], **kwargs: Any) -> dict[str, Any]:
        request = super()._prepare_request(messages, **kwargs)
        model_name = self.model.rsplit("/", 1)[-1].lower()
        if model_name.startswith("gemma-"):
            # Gemma rejects Gemini signatures carried by replayed tool calls or text.
            # Modify the converted request so persisted messages remain intact.
            for content in request["contents"]:
                for part in content.parts or []:
                    part.thought_signature = None
        elif model_name.startswith("gemini-") and model_name.endswith("-latest"):
            # LangChain's version-based signature repair misses unversioned aliases.
            # Only the first parallel call in each step of the active turn needs one.
            for content in reversed(request["contents"]):
                parts = content.parts or []
                if content.role == "user" and not any(part.function_response for part in parts):
                    break
                if content.role == "model":
                    for part in parts:
                        if part.function_call:
                            if not part.thought_signature:
                                # Keep the documented sentinel literal on the wire;
                                # bytes would be base64-encoded by the Google SDK.
                                part.thought_signature = cast("bytes", "skip_thought_signature_validator")
                            break
        return request
