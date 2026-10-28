"""Exercise subscription function-call streams without live credentials."""

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

import httpx
from langchain_core.messages import HumanMessage
from openai import AsyncOpenAI, OpenAI
import pytest
import pytest_asyncio

from agent.modules.providers.chatgpt.chat_model import ChatGPTChatModel
from agent.modules.tools.coding.adapter import make_coding_tool, prepare_tool_batch
from agent.modules.tools.coding.models import InvocationContext
from agent.modules.tools.coding.service import CodingService


def tool_events(layout, *, terminal="completed", path="README.md"):
    items = [{"id": f"fc_{index}", "call_id": f"call_{index}", "type": "function_call",
              "name": name, "arguments": json.dumps(args), "status": "completed"}
             for index, (name, args) in enumerate([
                 ("read", {"path": path}), ("read", {"path": "AGENTS.md"}), ("list_dir", {}),
             ])]
    response = {"id": "resp_test", "object": "response", "model": "gpt-test", "created_at": 1,
                "status": "in_progress", "output": [], "parallel_tool_calls": True,
                "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
                          "input_tokens_details": {"cached_tokens": 0},
                          "output_tokens_details": {"reasoning_tokens": 0}}}
    events = [{"type": "response.created", "response": response}]

    def added(index):
        return {"type": "response.output_item.added", "output_index": index,
                "item": {**items[index], "status": "in_progress",
                         "arguments": items[index]["arguments"] if layout == "initial_arguments" else ""}}

    def delta(index, text):
        return {"type": "response.function_call_arguments.delta", "output_index": index,
                "item_id": items[index]["id"], "delta": text}

    def arguments_done(index):
        return {"type": "response.function_call_arguments.done", "output_index": index,
                "item_id": items[index]["id"], "arguments": items[index]["arguments"]}

    def item_done(index, output_index=None):
        return {"type": "response.output_item.done", "output_index": index if output_index is None else output_index,
                "item": items[index]}

    if layout == "interleaved":
        events.extend(added(index) for index in range(3))
        events.extend(delta(index, items[index]["arguments"][:5]) for index in range(3))
        events.extend(delta(index, items[index]["arguments"][5:]) for index in reversed(range(3)))
        events.extend(arguments_done(index) for index in range(3))
        events.extend(item_done(index, index + 10) for index in (2, 0, 1))
    elif layout != "completed_output_only":
        for index, item in enumerate(items):
            events.append(added(index))
            if layout in {"delta_and_done", "delta_only", "partial_delta"}:
                text = item["arguments"][:5] if layout == "partial_delta" else item["arguments"]
                events.append(delta(index, text))
            if layout in {"done", "arguments_done_only", "delta_and_done", "partial_delta"}:
                events.append(arguments_done(index))
            if layout in {"done", "item_done_only", "delta_and_done", "partial_delta"}:
                events.append(item_done(index))
    if terminal:
        output = items if layout in {"completed_output_only", "headers_and_completed_output"} else []
        final = {**response, "status": terminal, "output": output}
        if terminal == "failed":
            final["error"] = {"code": "subscription_sharing_usage_limit_exceeded", "message": "Usage limit"}
        events.append({"type": f"response.{terminal}", "response": final})
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events)


@pytest_asyncio.fixture
async def streamed_model():
    clients = []

    def build(events, *, headers=False):
        def respond(request):
            text = events(request) if callable(events) else events
            return httpx.Response(200, headers={"content-type": "text/event-stream", "x-diagnostic": "retained"}, text=text)

        transport = httpx.MockTransport(respond)
        client = OpenAI(api_key="mock-only", http_client=httpx.Client(transport=transport), max_retries=0)
        async_client = AsyncOpenAI(api_key="mock-only", http_client=httpx.AsyncClient(transport=transport), max_retries=0)
        clients.append((client, async_client))
        return ChatGPTChatModel(model="gpt-test", api_key="mock-only", use_responses_api=True, streaming=True,
                                root_client=client, root_async_client=async_client,
                                client=client.chat.completions, async_client=async_client.chat.completions,
                                include_response_headers=headers)

    yield build
    for client, async_client in clients:
        client.close()
        await async_client.close()


async def invoke_mode(model, mode, messages):
    if mode == "invoke":
        return model.invoke(messages)
    if mode == "ainvoke":
        return await model.ainvoke(messages)
    chunks = list(model.stream(messages)) if mode == "stream" else [chunk async for chunk in model.astream(messages)]
    result = chunks[0]
    for chunk in chunks[1:]:
        result += chunk
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["invoke", "ainvoke", "stream", "astream"])
@pytest.mark.parametrize("headers", [False, True])
@pytest.mark.parametrize("layout", [
    "done", "arguments_done_only", "item_done_only", "delta_and_done", "partial_delta", "interleaved",
    "delta_only", "completed_output_only", "headers_and_completed_output", "initial_arguments",
])
async def test_function_arguments_survive_all_subscription_stream_layouts(streamed_model, mode, headers, layout):
    model = streamed_model(tool_events(layout), headers=headers)
    bound = model.bind_tools([make_coding_tool("read"), make_coding_tool("list_dir")])
    result = await invoke_mode(bound, mode, [HumanMessage(content="Read two files and list the workspace.")])
    calls = {call["id"]: call for call in result.tool_calls}
    assert len(result.tool_calls) == 3 and not result.invalid_tool_calls
    assert calls["call_0"]["name"] == calls["call_1"]["name"] == "read"
    assert calls["call_0"]["args"] == {"path": "README.md"}
    assert calls["call_1"]["args"] == {"path": "AGENTS.md"}
    assert calls["call_2"]["name"] == "list_dir" and calls["call_2"]["args"] == {}
    blocks = [block for block in result.content if block.get("type") == "function_call"]
    assert len(blocks) == 3
    assert {block["call_id"]: json.loads(block["arguments"]) for block in blocks} == {
        call_id: call["args"] for call_id, call in calls.items()
    }
    assert result.usage_metadata["total_tokens"] == 12
    if headers:
        assert result.response_metadata["headers"]["x-diagnostic"] == "retained"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["invoke", "ainvoke", "stream", "astream"])
@pytest.mark.parametrize("terminal", ["incomplete", "failed", None])
async def test_function_calls_do_not_bypass_terminal_errors(streamed_model, mode, terminal):
    model = streamed_model(tool_events("done", terminal=terminal))
    with pytest.raises(RuntimeError, match="incomplete|Manage usage|without response.completed"):
        await invoke_mode(model, mode, [HumanMessage(content="Read files.")])


@pytest.mark.asyncio
async def test_concurrent_calls_keep_sdk_clients_and_arguments_isolated(streamed_model):
    def events(request):
        path = "first.txt" if "first.txt" in request.content.decode() else "second.txt"
        return tool_events("done", path=path)

    model = streamed_model(events)
    client = model.root_async_client
    create = client.responses.create
    first, second = await asyncio.gather(*[
        model.ainvoke([HumanMessage(content=path)]) for path in ("first.txt", "second.txt")
    ])
    assert first.tool_calls[0]["args"] == {"path": "first.txt"}
    assert second.tool_calls[0]["args"] == {"path": "second.txt"}
    assert model.root_async_client is client and client.responses.create == create


@pytest.mark.asyncio
async def test_normalized_calls_pass_batch_validation_and_execute(streamed_model, tmp_path, isolated_container, monkeypatch):
    service = CodingService(tmp_path / "runtime")
    isolated_container._coding_service = service
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("README.md", "AGENTS.md"):
        (workspace / name).write_text(f"Contents of {name}", encoding="utf-8")
    context = InvocationContext("default", str(workspace), "stream-batch")
    monkeypatch.setattr("agent.modules.tools.coding.adapter.invocation_context",
                        lambda runtime: replace(context, tool_call_id=runtime.tool_call_id))
    tools = [make_coding_tool("read"), make_coding_tool("list_dir")]
    model = streamed_model(tool_events("done")).bind_tools(tools)
    response = await model.ainvoke([HumanMessage(content="Read two files and list the workspace.")])
    runtime = SimpleNamespace(context={}, state={}, config={})
    try:
        assert await prepare_tool_batch(tools, response.tool_calls, runtime) is None
        definitions = {tool.name: tool.coding_definition for tool in tools}
        for call in response.tool_calls:
            result = await service.invoke(definitions[call["name"]], call["args"], replace(context, tool_call_id=call["id"]))
            assert result.status == "success", result.content
    finally:
        await service.processes.close()
        await service.storage.close()
