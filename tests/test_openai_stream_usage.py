"""Replay gateway streams through the SDK and application usage tracking."""

import json
from functools import reduce
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from openai import AsyncOpenAI, OpenAI

from agent.modules.providers.models import ModelConfig
from agent.modules.providers.openai_compatible.factory import OpenAICompatibleFactory
from agent.modules.providers.provider import ProviderConfig, ProviderType
from agent.modules.usage import tracking, with_usage_tracking
from agent.modules.workflows import model_context


def usage(prompt=3371, completion=40, cached=3328):
    return {
        "prompt_tokens": prompt, "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_tokens_details": {"cached_tokens": cached},
        "completion_tokens_details": {"reasoning_tokens": 10},
    }


def stream_body(snapshots, *, tool_call=False):
    chunks = [
        {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "OK"}, "finish_reason": None}]},
    ]
    if tool_call:
        chunks = [{"choices": [{"index": 0, "finish_reason": None, "delta": {
            "role": "assistant", "tool_calls": [{"index": 0, "id": "call-read", "type": "function",
                "function": {"name": "read", "arguments": '{"path":'}}],
        }}]}, {"choices": [{"index": 0, "finish_reason": None, "delta": {
            "tool_calls": [{"index": 0, "function": {"arguments": '"source.py"}'}}],
        }}]}]
    finish = "tool_calls" if tool_call else "stop"
    if snapshots:
        chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": snapshots[0]})
        chunks.extend({"choices": [], "usage": snapshot} for snapshot in snapshots[1:])
    else:
        chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
    frames = ["data: " + json.dumps({
        "id": "test-completion", "object": "chat.completion.chunk", "created": 1,
        "model": "oc/deepseek-v4.1-flash-free", **chunk,
    }) + "\n\n" for chunk in chunks]
    return "".join(frames) + "data: [DONE]\n\n"


@pytest.fixture
def gateway_model():
    models = []

    def create(snapshots, *, tool_call=False):
        requests = []

        def handle(request):
            payload = json.loads(request.content)
            requests.append(payload)
            if not payload.get("stream"):
                return httpx.Response(200, json={
                    "id": "test-completion", "object": "chat.completion", "created": 1,
                    "model": "test-model", "usage": snapshots[-1],
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop"}],
                })
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                text=stream_body(snapshots, tool_call=tool_call))

        transport = httpx.MockTransport(handle)
        provider = ProviderConfig(name="gateway", provider_type=ProviderType.OPENAI_COMPATIBLE,
            base_url="http://gateway.test/v1", api_key="test-key", default_model="oc/deepseek-v4.1-flash-free")
        model = OpenAICompatibleFactory().create(provider, ModelConfig(model_name=provider.default_model), provider.api_key)
        sync_client = OpenAI(api_key="test-key", base_url=provider.base_url,
            http_client=httpx.Client(transport=transport))
        async_client = AsyncOpenAI(api_key="test-key", base_url=provider.base_url,
            http_client=httpx.AsyncClient(transport=transport))
        model.client = sync_client.chat.completions
        model.async_client = async_client.chat.completions
        models.append(sync_client)
        return model, requests

    yield create
    for client in models:
        client.close()


MESSAGES = [SystemMessage(content="Reply OK"), HumanMessage(content="Check usage")]


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshots", [
    [usage()],
    [usage(5371), usage()],
    [usage(5371), usage(), usage()],
    [usage(1000, 0, 0), usage(3000, 20), usage()],
])
async def test_async_stream_uses_the_final_usage_snapshot(gateway_model, snapshots, monkeypatch):
    model, requests = gateway_model(snapshots)
    records, events = [], []
    monkeypatch.setattr(tracking, "_schedule_record", records.append)
    monkeypatch.setattr(model_context, "get_stream_writer", lambda: events.append)
    config = with_usage_tracking({"configurable": {"thread_id": "usage-audit"}},
        agent_name="default", provider_name="gateway", model_name=model.model_name)
    chunks = [chunk async for chunk in model.astream(MESSAGES, config=config)]
    response = reduce(lambda left, right: left + right, chunks)
    model_context.emit_reported_context_usage(response, resolved=SimpleNamespace(
        provider_name="gateway", model_name=model.model_name), config=config)
    assert response.content == "OK"
    assert response.usage_metadata["input_tokens"] == records[0].input_tokens == 3371
    assert response.usage_metadata["output_tokens"] == records[0].output_tokens == 40
    assert response.usage_metadata["input_token_details"]["cache_read"] == 3328
    assert response.usage_metadata["output_token_details"]["reasoning"] == 10
    assert events[0]["current_context_tokens"] == 3411
    assert requests[0]["stream_options"]["include_usage"] is True


def test_sync_stream_uses_the_final_usage_snapshot(gateway_model):
    model, _ = gateway_model([usage(5371), usage()])
    response = reduce(lambda left, right: left + right, model.stream(MESSAGES))
    assert response.content == "OK"
    assert response.usage_metadata["input_tokens"] == 3371
    assert response.usage_metadata["total_tokens"] == 3411


@pytest.mark.asyncio
async def test_streaming_ainvoke_uses_the_final_usage_snapshot(gateway_model):
    model, _ = gateway_model([usage(5371), usage()])
    model.streaming = True
    response = await model.ainvoke(MESSAGES)
    assert response.content == "OK"
    assert response.usage_metadata["input_tokens"] == 3371
    assert response.usage_metadata["output_tokens"] == 40


@pytest.mark.asyncio
async def test_stream_without_usage_stays_unavailable(gateway_model):
    model, _ = gateway_model([])
    response = reduce(lambda left, right: left + right,
        [chunk async for chunk in model.astream(MESSAGES)])
    assert response.content == "OK"
    assert response.usage_metadata is None


def test_streaming_sync_invoke_uses_the_final_usage_snapshot(gateway_model):
    model, _ = gateway_model([usage(5371), usage()])
    model.streaming = True
    response = model.invoke(MESSAGES)
    assert response.usage_metadata["input_tokens"] == 3371
    assert response.usage_metadata["output_tokens"] == 40


@pytest.mark.asyncio
async def test_tool_call_arguments_and_finish_reason_survive_usage_normalization(gateway_model):
    model, _ = gateway_model([usage(5371), usage()], tool_call=True)
    response = reduce(lambda left, right: left + right,
        [chunk async for chunk in model.astream(MESSAGES)])
    assert response.tool_calls == [{"name": "read", "args": {"path": "source.py"},
        "id": "call-read", "type": "tool_call"}]
    assert response.response_metadata["finish_reason"] == "tool_calls"
    assert response.usage_metadata["total_tokens"] == 3411


@pytest.mark.asyncio
async def test_nonstreaming_response_usage_is_preserved(gateway_model):
    model, requests = gateway_model([usage()])
    response = await model.ainvoke(MESSAGES)
    assert requests[0]["stream"] is False
    assert response.content == "OK"
    assert response.usage_metadata["input_tokens"] == 3371
    assert response.usage_metadata["output_tokens"] == 40
