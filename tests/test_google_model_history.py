"""Replay signed Gemini history through Google's SDK after model switches."""

import base64
from functools import partial, reduce
import json

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent.modules.providers.google.chat_model import GoogleChatModel
from agent.modules.providers.google.factory import GoogleFactory
from agent.modules.providers.models import ModelConfig
from agent.modules.providers.provider import ProviderConfig, ProviderType
from agent.modules.workflows.message_history import normalize_messages_for_chat_model


SIGNATURE = base64.b64encode(b"gemini-thought-signature").decode("ascii")
SIGNATURES_KEY = "__gemini_function_call_thought_signatures__"
ECHO_TOOL = {
    "name": "echo",
    "description": "Return the supplied text.",
    "parameters": {
        "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
    },
}


def signed_history():
    return [
        SystemMessage(content="Finish the requested task."),
        HumanMessage(content="Call echo with hello then say done."),
        AIMessage(
            content=[{"type": "text", "text": "Calling echo.", "extras": {"signature": SIGNATURE}}],
            tool_calls=[{"name": "echo", "args": {"text": "hello"}, "id": "echo-call", "type": "tool_call"}],
            additional_kwargs={SIGNATURES_KEY: {"echo-call": SIGNATURE}},
            response_metadata={"model_name": "gemini-3-flash-preview", "model_provider": "google_genai"},
        ),
        ToolMessage(content="hello", name="echo", tool_call_id="echo-call"),
    ]


@pytest.mark.parametrize("model_name", ["gemma-4-26b-a4b-it", "gemini-3-flash-preview"])
@pytest.mark.parametrize("mode", ["invoke", "ainvoke", "stream", "astream"])
@pytest.mark.parametrize("normalize", [False, True])
@pytest.mark.asyncio
async def test_signed_history_at_google_http_boundary(monkeypatch, model_name, mode, normalize):
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        assert request.url.path.endswith(f"/{model_name}:" + (
            "streamGenerateContent" if mode in ("stream", "astream") else "generateContent"
        ))
        assert body["systemInstruction"]["parts"][0]["text"] == "Finish the requested task."
        assert body["generationConfig"]["temperature"] == 0
        assert body["tools"][0]["functionDeclarations"][0]["name"] == "echo"
        parts = body["contents"][1]["parts"]
        assert parts[0]["text"] == "Calling echo."
        assert parts[1]["functionCall"] == {"name": "echo", "args": {"text": "hello"}}
        assert body["contents"][2]["parts"][0]["functionResponse"]["name"] == "echo"
        if model_name.startswith("gemma-"):
            if any("thoughtSignature" in part for content in body["contents"] for part in content["parts"]):
                return httpx.Response(400, json={"error": {
                    "code": 400, "message": "Request contains an invalid argument.", "status": "INVALID_ARGUMENT",
                }})
        else:
            assert parts[1]["thoughtSignature"] == SIGNATURE
            if not normalize:
                assert parts[0]["thoughtSignature"] == SIGNATURE
        response = {"candidates": [{"content": {"role": "model", "parts": [{"text": "Done."}]},
                                     "finishReason": "STOP"}], "modelVersion": model_name}
        if mode in ("stream", "astream"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  text="data: " + json.dumps(response) + "\n\n")
        return httpx.Response(200, json=response)

    monkeypatch.setattr("agent.modules.providers.google.factory.GoogleChatModel", partial(
        GoogleChatModel, client_args={"transport": httpx.MockTransport(handle)},
    ))
    provider = ProviderConfig(name="google", provider_type=ProviderType.GOOGLE,
                              base_url="", api_key="test-key", default_model=model_name)
    model = GoogleFactory().create(provider, ModelConfig(model_name=model_name, disable_retries=True), "test-key")
    history = signed_history()
    original = [message.model_dump() for message in history]
    messages = normalize_messages_for_chat_model(history) if normalize else history
    try:
        bound = model.bind_tools([ECHO_TOOL])
        if mode == "invoke":
            response = bound.invoke(messages)
        elif mode == "ainvoke":
            response = await bound.ainvoke(messages)
        else:
            chunks = list(bound.stream(messages)) if mode == "stream" else [
                chunk async for chunk in bound.astream(messages)
            ]
            response = reduce(lambda left, right: left + right, chunks)
        assert response.text == "Done."
        assert len(calls) == 1
        assert [message.model_dump() for message in history] == original
    finally:
        await model.aclose()


@pytest.mark.parametrize("model_name", ["gemma-4-31b-it", "models/gemma-4-26b-a4b-it"])
@pytest.mark.asyncio
async def test_gemma_variants_accept_unstamped_signed_history(model_name):
    model = GoogleChatModel(model=model_name, google_api_key="test-key")
    history = signed_history()
    history[2].response_metadata = {}
    try:
        request = model._prepare_request(history, tools=[ECHO_TOOL])
        assert all(part.thought_signature is None for content in request["contents"] for part in content.parts)
        assert history[2].additional_kwargs[SIGNATURES_KEY]["echo-call"] == SIGNATURE
    finally:
        await model.aclose()
