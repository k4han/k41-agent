"""Replay provider cache fields through SDK normalization and context reporting."""

import asyncio
import json
from functools import reduce
from types import SimpleNamespace

import anthropic
import httpx
import httpx2
import pytest
from google.genai.types import GenerateContentResponse
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_google_genai.chat_models import _response_to_result
from openai import AsyncOpenAI, OpenAI

from agent.modules.providers.anthropic.factory import AnthropicFactory
from agent.modules.providers.models import ModelConfig
from agent.modules.providers.openai_compatible.factory import OpenAICompatibleFactory
from agent.modules.providers.provider import ProviderConfig, ProviderType
from agent.modules.usage.cache import cache_read_tokens
from agent.modules.usage.tracking import extract_usage
from agent.modules.workflows import model_context


def assert_report(response, monkeypatch, *, input_tokens, cached, output_tokens=40):
    events = []
    monkeypatch.setattr(model_context, "get_stream_writer", lambda: events.append)
    model_context.emit_reported_context_usage(response,
        resolved=SimpleNamespace(provider_name="test", model_name="test-model"), config={})
    stored = extract_usage(LLMResult(generations=[[ChatGeneration(message=response)]]))
    assert stored.input_tokens == events[0]["input_tokens"] == input_tokens
    assert stored.output_tokens == events[0]["output_tokens"] == output_tokens
    assert cache_read_tokens(stored.input_token_details, stored.input_tokens) == events[0]["cache_read_tokens"] == cached
    assert events[0]["current_context_tokens"] == input_tokens + output_tokens


@pytest.mark.parametrize("service_tier", [None, "priority", "flex"])
@pytest.mark.parametrize("mode", ["invoke", "ainvoke", "stream", "astream"])
@pytest.mark.asyncio
async def test_responses_endpoint_cache_usage(monkeypatch, service_tier, mode):
    def handle(request):
        assert request.url.path == "/v1/responses"
        response = {
            "id": "resp-test", "object": "response", "created_at": 1, "status": "completed",
            "model": "gpt-6", "error": None, "incomplete_details": None,
            "output": [{"id": "msg-test", "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "OK", "annotations": []}]}],
            "usage": {"input_tokens": 3371, "output_tokens": 40, "total_tokens": 3411,
                "input_tokens_details": {"cached_tokens": 3000, "cache_write_tokens": 100},
                "output_tokens_details": {"reasoning_tokens": 10}},
            "service_tier": service_tier or "default",
        }
        if not json.loads(request.content).get("stream"):
            return httpx.Response(200, json=response)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=
            "data: " + json.dumps({"type": "response.completed", "sequence_number": 1, "response": response}) + "\n\n")

    provider = ProviderConfig(name="test", provider_type=ProviderType.OPENAI,
        base_url="https://provider.test/v1", api_key="test-key", default_model="gpt-6")
    model = OpenAICompatibleFactory().create(provider, ModelConfig(model_name="gpt-6"), "test-key")
    transport = httpx.MockTransport(handle)
    with OpenAI(api_key="test-key", base_url=provider.base_url, http_client=httpx.Client(transport=transport)) as client:
        async with AsyncOpenAI(api_key="test-key", base_url=provider.base_url,
            http_client=httpx.AsyncClient(transport=transport)) as async_client:
            model.root_client, model.root_async_client = client, async_client
            if mode == "invoke":
                response = model.invoke("Hello")
            elif mode == "ainvoke":
                response = await model.ainvoke("Hello")
            else:
                chunks = list(model.stream("Hello")) if mode == "stream" else [chunk async for chunk in model.astream("Hello")]
                response = reduce(lambda left, right: left + right, chunks)
    assert_report(response, monkeypatch, input_tokens=3371, cached=3000)


def anthropic_body(deltas):
    message = {"id": "msg-test", "type": "message", "role": "assistant", "model": "claude-sonnet-4-5",
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 1,
            "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 200,
            "cache_creation": {"ephemeral_5m_input_tokens": 200, "ephemeral_1h_input_tokens": 0}}}
    events = [
        {"type": "message_start", "message": message},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "OK"}},
        {"type": "content_block_stop", "index": 0},
        *[{"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": usage} for usage in deltas],
        {"type": "message_stop"},
    ]
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)


@pytest.mark.parametrize("deltas, input_tokens, cached", [
    ([{"output_tokens": 40}], 1300, 1000),
    ([{"output_tokens": 20}, {"output_tokens": 40}], 1300, 1000),
    ([{"input_tokens": 150, "cache_read_input_tokens": 2000, "cache_creation_input_tokens": 250,
        "output_tokens": 40}], 2400, 2000),
    ([{"cache_read_input_tokens": 2000, "output_tokens": 20},
      {"cache_read_input_tokens": 1800, "output_tokens": 40}], 2100, 1800),
])
@pytest.mark.parametrize("mode", ["stream", "astream", "concurrent"])
@pytest.mark.asyncio
async def test_anthropic_stream_merges_start_usage_and_final_cumulative_totals(
    monkeypatch, deltas, input_tokens, cached, mode,
):
    def handle(request):
        assert request.url.path == "/v1/messages"
        body = json.loads(request.content)
        # Different totals expose request-state leaks when a model is reused concurrently.
        alternate = body["messages"][-1]["content"] == "Other"
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
            text=anthropic_body([{"cache_read_input_tokens": 500, "output_tokens": 40}] if alternate else deltas))

    provider = ProviderConfig(name="test", provider_type=ProviderType.ANTHROPIC,
        base_url="", api_key="test-key", default_model="claude-sonnet-4-5")
    model = AnthropicFactory().create(provider, ModelConfig(model_name=provider.default_model), "test-key")
    transport = httpx2.MockTransport(handle)
    with anthropic.Anthropic(api_key="test-key", base_url="https://provider.test",
        http_client=httpx2.Client(transport=transport)) as client:
        async with anthropic.AsyncAnthropic(api_key="test-key", base_url="https://provider.test",
            http_client=httpx2.AsyncClient(transport=transport)) as async_client:
            model._client, model._async_client = client, async_client
            async def collect(prompt):
                chunks = []
                async for chunk in model.astream(prompt):
                    chunks.append(chunk)
                    await asyncio.sleep(0)
                return reduce(lambda left, right: left + right, chunks)
            if mode == "stream":
                response = reduce(lambda left, right: left + right, model.stream("Hello"))
            elif mode == "astream":
                response = await collect("Hello")
            else:
                response, other = await asyncio.gather(collect("Hello"), collect("Other"))
                assert_report(other, monkeypatch, input_tokens=800, cached=500)
    assert response.content == "OK"
    assert_report(response, monkeypatch, input_tokens=input_tokens, cached=cached)


@pytest.mark.parametrize("cached", [0, 3000])
@pytest.mark.parametrize("stream", [False, True])
def test_gemini_cache_tokens_are_already_part_of_prompt_tokens(monkeypatch, cached, stream):
    def result(output_tokens):
        return GenerateContentResponse.model_validate({
            "candidates": [{"content": {"role": "model", "parts": [{"text": "OK"}]}}],
            "usageMetadata": {"promptTokenCount": 3371, "cachedContentTokenCount": cached,
                "candidatesTokenCount": output_tokens, "totalTokenCount": 3371 + output_tokens},
        })
    if stream:
        first = _response_to_result(result(20), stream=True).generations[0].message
        last = _response_to_result(result(40), stream=True, prev_usage=first.usage_metadata).generations[0].message
        response = first + last
    else:
        response = _response_to_result(result(40)).generations[0].message
    assert_report(response, monkeypatch, input_tokens=3371, cached=cached)


@pytest.mark.parametrize("cached", [0, 1000])
def test_anthropic_nonstreaming_total_includes_cache_reads_and_creation(monkeypatch, cached):
    provider = ProviderConfig(name="test", provider_type=ProviderType.ANTHROPIC,
        base_url="", api_key="test-key", default_model="claude-sonnet-4-5")
    model = AnthropicFactory().create(provider, ModelConfig(model_name=provider.default_model), "test-key")
    raw_message = anthropic.types.Message.model_validate({
        "id": "msg-test", "type": "message", "role": "assistant", "model": provider.default_model,
        "content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 40,
            "cache_read_input_tokens": cached, "cache_creation_input_tokens": 200},
    })
    response = model._format_output(raw_message).generations[0].message
    assert_report(response, monkeypatch, input_tokens=300 + cached, cached=cached)


@pytest.mark.parametrize("raw_usage, expected", [
    ({"prompt_tokens": 1000, "completion_tokens": 40,
      "prompt_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 100}}, 800),
    ({"input_tokens": 1000, "output_tokens": 40,
      "input_tokens_details": {"cached_tokens": 800}}, 800),
    ({"prompt_tokens": 1000, "completion_tokens": 40, "prompt_cache_hit_tokens": 800}, 800),
    ({"prompt_tokens": 1000, "completion_tokens": 40,
      "prompt_tokens_details": {"cache_write_tokens": 800}}, None),
])
def test_raw_usage_fallback_retains_cache_details(raw_usage, expected):
    usage = extract_usage(LLMResult(generations=[[]], llm_output={"token_usage": raw_usage}))
    assert usage.input_tokens == 1000
    assert usage.total_tokens == 1040
    assert cache_read_tokens(usage.input_token_details, usage.input_tokens) == expected


@pytest.mark.parametrize("details, expected", [
    ({"cache_read": 100, "priority_cache_read": 200, "flex_cache_read": 300}, 600),
    ({"cache_creation": 800}, None), ({"cache_read": 0, "cache_creation": 800}, 0),
    ({"cache_read": None}, None), ({"cache_read": -1}, None), ({"cache_read": True}, None),
    ({"cache_read": "100"}, None), ({"cache_read": 1.5}, None), ({"cache_read": 1001}, None),
])
def test_cache_read_partitions_exclude_writes_and_invalid_values(details, expected):
    assert cache_read_tokens(details, 1000) == expected
