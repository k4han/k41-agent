"""Reasoning from real provider wire formats reaches the runtime's thinking stream."""

import json
from functools import reduce

import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from openai import AsyncOpenAI, OpenAI

from agent.modules.agent_runtime.runner import _StreamingChunkExtractor
from agent.modules.providers.google.factory import GoogleFactory
from agent.modules.providers.models import ModelConfig
from agent.modules.providers.openai_compatible.factory import OpenAICompatibleFactory
from agent.modules.providers.provider import ProviderConfig, ProviderType
from agent.shared.infrastructure.parsing import extract_final_text_content, extract_message_thinking

MESSAGES = [HumanMessage(content="Is 1001 prime?")]


def _sse(chunks: list[dict]) -> str:
    frames = [
        "data: " + json.dumps({
            "id": "test-completion", "object": "chat.completion.chunk", "created": 1,
            "model": "test-model", **chunk,
        }) + "\n\n"
        for chunk in chunks
    ]
    return "".join(frames) + "data: [DONE]\n\n"


def _delta(**delta) -> dict:
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}


@pytest.fixture
def gateway_model():
    clients = []

    def create(field: str):
        def handle(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            if not payload.get("stream"):
                return httpx.Response(200, json={
                    "id": "test-completion", "object": "chat.completion", "created": 1, "model": "test-model",
                    "choices": [{"index": 0, "finish_reason": "stop", "message": {
                        "role": "assistant", "content": "No.", field: "7 x 143 = 1001.",
                    }}],
                })
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=_sse([
                _delta(role="assistant", content="", **{field: "7 x 143"}),
                _delta(content="", **{field: " = 1001."}),
                _delta(content="No."),
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]))

        transport = httpx.MockTransport(handle)
        provider = ProviderConfig(name="gateway", provider_type=ProviderType.OPENAI_COMPATIBLE,
            base_url="http://gateway.test/v1", api_key="test-key", default_model="test-model")
        model = OpenAICompatibleFactory().create(provider, ModelConfig(model_name="test-model"), "test-key")
        sync_client = OpenAI(api_key="test-key", base_url=provider.base_url,
            http_client=httpx.Client(transport=transport))
        async_client = AsyncOpenAI(api_key="test-key", base_url=provider.base_url,
            http_client=httpx.AsyncClient(transport=transport))
        model.client = sync_client.chat.completions
        model.async_client = async_client.chat.completions
        clients.append(sync_client)
        return model

    yield create
    for client in clients:
        client.close()


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning"])
@pytest.mark.asyncio
async def test_gateway_reasoning_streams_as_thinking(gateway_model, field) -> None:
    model = gateway_model(field)
    extractor = _StreamingChunkExtractor()
    chunks = [chunk async for chunk in model.astream(MESSAGES)]
    deltas = [extractor.extract((chunk, {})) for chunk in chunks]

    assert "".join(delta.thinking for delta in deltas) == "7 x 143 = 1001."
    assert "".join(delta.text for delta in deltas) == "No."
    # The merged message is what the checkpointer saves for history.
    message = reduce(lambda left, right: left + right, chunks)
    assert extract_message_thinking(message) == "7 x 143 = 1001."
    assert extract_final_text_content(message.content) == "No."


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning"])
@pytest.mark.asyncio
async def test_gateway_reasoning_survives_non_streaming_calls(gateway_model, field) -> None:
    message = await gateway_model(field).ainvoke(MESSAGES)

    assert extract_message_thinking(message) == "7 x 143 = 1001."
    assert message.content == "No."


def test_message_thinking_combines_content_blocks_and_gateway_reasoning() -> None:
    message = AIMessage(
        content=[{"type": "thinking", "thinking": "Block."}, {"type": "text", "text": "Answer"}],
        additional_kwargs={"reasoning_content": "Gateway."},
    )

    assert extract_message_thinking(message) == "Block.\n\nGateway."
    assert extract_message_thinking(AIMessage(content="Plain")) == ""


def test_streaming_extractor_reads_gateway_reasoning_kwargs() -> None:
    chunk = AIMessageChunk(content="", additional_kwargs={"reasoning_content": "step"})

    delta = _StreamingChunkExtractor().extract((chunk, {}))

    assert (delta.text, delta.thinking) == ("", "step")


@pytest.mark.parametrize(("model_name", "expected"), [
    ("gemini-flash-latest", True),
    ("gemini-2.5-pro", True),
    ("gemini-3.1-pro-preview", True),
    ("models/gemini-3-flash", True),
    ("gemini-2.0-flash", None),
    ("gemini-1.5-pro", None),
    ("gemma-4-31b-it", None),
])
def test_gemini_thinking_models_request_thought_summaries(model_name, expected) -> None:
    provider = ProviderConfig(name="google", provider_type=ProviderType.GOOGLE,
                              base_url="", api_key="test-key", default_model=model_name)

    model = GoogleFactory().create(provider, ModelConfig(model_name=model_name), "test-key")

    assert model.include_thoughts is expected
