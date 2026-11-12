"""LangGraph v3 execution and the application's serializable stream projections."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_protocol.protocol import MessagesData
from langgraph.prebuilt import ToolCallTransformer
from langgraph.stream import CustomTransformer, ProtocolEvent

from agent.shared.infrastructure.parsing import extract_message_thinking


def protocol_event(method: str, data: Any, namespace=()) -> ProtocolEvent:
    return {
        "type": "event",
        "method": method,
        "params": {
            "namespace": list(namespace),
            "timestamp": int(time.time() * 1000),
            "data": jsonable_encoder(data),
        },
    }


class EventSequence:
    """Assign one order to native, application, and delegated events."""

    def __init__(self, run_id: str | None = None) -> None:
        self.run_id = run_id or uuid4().hex
        self.seq = 0

    def stamp(self, event: dict[str, Any]) -> dict[str, Any]:
        self.seq += 1
        return {
            **jsonable_encoder(event),
            "seq": self.seq,
            "event_id": f"{self.run_id}:{self.seq}",
        }


class ACPMessageEncoder:
    """Encode ACP live updates as native content-block and tool events."""

    def __init__(self) -> None:
        self.message_id = ""
        self.block_type = ""
        self.index = -1
        self.content = ""
        self.text = ""

    def _event(self, data: MessagesData) -> ProtocolEvent:
        return protocol_event("messages", [data, {"run_id": self.message_id}])

    def close_block(self):
        if not self.block_type:
            return []
        kind = self.block_type
        event = self._event(
            {
                "event": "content-block-finish",
                "index": self.index,
                "content": {"type": kind, kind: self.content},
            }
        )
        self.block_type = ""
        return [event]

    def finish(self):
        if not self.message_id:
            return []
        events = self.close_block()
        events.append(self._event({"event": "message-finish"}))
        self.message_id = ""
        self.index = -1
        self.text = ""
        return events

    def delta(self, content: str, kind="text"):
        if not content:
            return []
        events = []
        if not self.message_id:
            self.message_id = uuid4().hex
            events.append(
                self._event(
                    {"event": "message-start", "role": "ai", "id": self.message_id}
                )
            )
        if self.block_type != kind:
            events.extend(self.close_block())
            self.index += 1
            self.block_type, self.content = kind, ""
            events.append(
                self._event(
                    {
                        "event": "content-block-start",
                        "index": self.index,
                        "content": {"type": kind, kind: ""},
                    }
                )
            )
        self.content += content
        if kind == "text":
            self.text += content
        events.append(
            self._event(
                {
                    "event": "content-block-delta",
                    "index": self.index,
                    "delta": {"type": f"{kind}-delta", kind: content},
                }
            )
        )
        return events

    def encode(self, event):
        kind = event.get("type")
        if kind in {"message", "thinking"}:
            return self.delta(
                str(event.get("content") or ""),
                "reasoning" if kind == "thinking" else "text",
            )
        if kind == "final":
            events = []
            if not self.text and not event.get("already_streamed"):
                events.extend(self.delta(str(event.get("content") or "")))
            return events + self.finish()
        if kind == "tool_call":
            return self.finish() + [
                protocol_event(
                    "tools",
                    {
                        "event": "tool-started",
                        "tool_call_id": event.get("id"),
                        "tool_name": event.get("name"),
                        "input": event.get("args") or {},
                    },
                )
            ]
        if kind == "tool_result":
            return [
                protocol_event(
                    "tools",
                    {
                        "event": "tool-finished",
                        "tool_call_id": event.get("tool_call_id"),
                        "output": {
                            "content": event.get("content"),
                            "name": event.get("name"),
                            "artifact": {"browser": event.get("browser_artifact")},
                            "status": event.get("status", "success"),
                        },
                    },
                )
            ]
        return []


def whole_message_events(message: AIMessage, metadata: dict) -> list[dict]:
    metadata = metadata if isinstance(metadata, dict) else {}
    message_id = str(message.id or metadata.get("run_id") or uuid4().hex)
    metadata = {**metadata, "run_id": metadata.get("run_id") or message_id}
    payloads = [{"event": "message-start", "role": "ai", "id": message_id}]
    blocks = list(message.content_blocks)
    thinking = extract_message_thinking(message)
    if thinking and not any(block.get("type") == "reasoning" for block in blocks):
        blocks.insert(0, {"type": "reasoning", "reasoning": thinking})
    for index, block in enumerate(blocks):
        payloads.extend(
            [
                {"event": "content-block-start", "index": index, "content": block},
                {"event": "content-block-finish", "index": index, "content": block},
            ]
        )
    payloads.append({"event": "message-finish", "usage": message.usage_metadata})
    return [protocol_event("messages", [payload, metadata]) for payload in payloads]


async def stream_graph(
    graph, input_data, *, config, stream_mode, protocol_events=False, **kwargs
):
    """Drive v3 once and normalize graph events for application consumers."""
    modes = {stream_mode} if isinstance(stream_mode, str) else set(stream_mode)
    started_messages: set[str] = set()
    finished_tools: set[str] = set()
    seen_values: set[str] = set()
    first_values = True
    acp = ACPMessageEncoder()
    async with await graph.astream_events(
        input_data,
        config=config,
        version="v3",
        transformers=[CustomTransformer, ToolCallTransformer],
        **kwargs,
    ) as run:
        async for event in run:
            method, params = event["method"], event["params"]
            namespace, data = params["namespace"], params["data"]
            native = []
            chunk = None
            if (
                method == "custom"
                and isinstance(data, dict)
                and data.get("type") == "subagent_event"
            ):
                child = data["event"]
                child_params = child["params"]
                child = {
                    **child,
                    "params": {
                        **child_params,
                        "namespace": [
                            *namespace,
                            f"{data['agent_name']}:{data['delegation_id']}",
                            *child_params["namespace"],
                        ],
                    },
                }
                if child["method"] == "lifecycle" and not child_params["namespace"]:
                    child["params"]["data"] = {
                        **child_params["data"],
                        "delegation_id": data["delegation_id"],
                        "agent_name": data["agent_name"],
                        "tool_call_id": data["tool_call_id"],
                        "thread_id": data["thread_id"],
                        "cause": {
                            "type": "tool-call",
                            "tool_call_id": data["tool_call_id"],
                        },
                    }
                native.append(child)
            elif method == "messages":
                payload, metadata = data
                metadata = metadata if isinstance(metadata, dict) else {}
                if metadata.get(
                    "context_compaction"
                ) or "context_compaction" in metadata.get("tags", []):
                    continue
                if isinstance(payload, AIMessage) and not isinstance(
                    payload, AIMessageChunk
                ):
                    if (
                        not payload.additional_kwargs.get("is_compact_summary")
                        and not payload.additional_kwargs.get("acp_live")
                        and str(payload.id) not in started_messages
                    ):
                        native.extend(whole_message_events(payload, metadata))
                        started_messages.add(str(payload.id))
                elif isinstance(payload, dict):
                    native.append(
                        {**event, "params": {**params, "data": [payload, metadata]}}
                    )
                    if payload.get("event") == "message-start":
                        started_messages.add(
                            str(payload.get("id") or metadata.get("run_id"))
                        )
                    if payload.get("event") == "content-block-delta":
                        delta = payload.get("delta") or {}
                        if delta.get("type") == "text-delta":
                            chunk = AIMessageChunk(content=delta.get("text", ""))
                        elif delta.get("type") == "reasoning-delta":
                            chunk = AIMessageChunk(
                                content=[
                                    {
                                        "type": "reasoning",
                                        "reasoning": delta.get("reasoning", ""),
                                    }
                                ]
                            )
                    if payload.get("event") == "message-finish":
                        chunk = AIMessageChunk(content="")
                        metadata = {**metadata, "message_finished": True}
                    if chunk is not None:
                        chunk = (chunk, metadata)
            elif (
                method == "custom"
                and isinstance(data, dict)
                and data.get("type") == "acp_live"
            ):
                native.extend(acp.encode(data.get("event") or {}))
            elif (
                method == "lifecycle"
                and isinstance(data, dict)
                and data.get("namespace")
            ):
                native.append(
                    {**event, "params": {**params, "namespace": data["namespace"]}}
                )
            else:
                native.append(event)
            if (
                method == "tools"
                and isinstance(data, dict)
                and data.get("event") in {"tool-finished", "tool-error"}
            ):
                finished_tools.add(str(data.get("tool_call_id")))
            if method == "values" and not namespace and isinstance(data, dict):
                for message in data.get("messages", []):
                    key = str(getattr(message, "id", "") or id(message))
                    if key in seen_values:
                        continue
                    seen_values.add(key)
                    if first_values or getattr(message, "additional_kwargs", {}).get(
                        "is_compact_summary"
                    ):
                        continue
                    if isinstance(message, AIMessage) and key not in started_messages:
                        # ACP publishes live events and saves the same messages separately.
                        from agent.modules.acp import ACP_LIVE_KWARG

                        if not message.additional_kwargs.get(ACP_LIVE_KWARG):
                            native.extend(whole_message_events(message, {}))
                            started_messages.add(key)
                    elif (
                        isinstance(message, ToolMessage)
                        and message.tool_call_id not in finished_tools
                    ):
                        native.append(
                            protocol_event(
                                "tools",
                                {
                                    "event": "tool-finished",
                                    "tool_call_id": message.tool_call_id,
                                    "output": message,
                                },
                            )
                        )
                        finished_tools.add(message.tool_call_id)
                first_values = False
            if protocol_events:
                for item in native:
                    yield "protocol", jsonable_encoder(item)
            if namespace:
                continue
            if method == "messages" and chunk is not None and "messages" in modes:
                yield "messages", chunk
            elif method in modes and method != "messages":
                if method == "values" and params.get("interrupts"):
                    data = {**data, "__interrupt__": params["interrupts"]}
                yield data if isinstance(stream_mode, str) else (method, data)
        await run.output()
        if protocol_events:
            latest_config = {
                **config,
                "configurable": dict(config.get("configurable", {})),
            }
            latest_config["configurable"].pop("checkpoint_id", None)
            snapshot = (
                await graph.aget_state(latest_config)
                if getattr(graph, "checkpointer", None)
                else None
            )
            checkpoint_id = (
                (getattr(snapshot, "config", {}) or {})
                .get("configurable", {})
                .get("checkpoint_id")
            )
            yield (
                "protocol",
                protocol_event(
                    "custom:k41",
                    {
                        "type": "run_state",
                        "interrupted": await run.interrupted(),
                        "checkpoint_id": checkpoint_id,
                    },
                ),
            )


def application_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Read a root application event from the v3 envelope."""
    if event.get("type") != "event":
        return None
    params = event.get("params") or {}
    if event.get("method") == "custom:k41" and not params.get("namespace"):
        data = params.get("data") or {}
        if data.get("projection") == "ui":
            return {key: value for key, value in data.items() if key != "projection"}
    return None


async def run_agent_protocol_stream(*, edit=False, **params) -> AsyncIterator[dict[str, Any]]:
    """Expose native v3 events alongside normalized application updates."""
    from agent.modules.agent_runtime.runner import (
        run_agent_edit_stream,
        run_agent_stream,
    )
    from agent.shared.infrastructure.errors import classify_agent_error

    sequence = EventSequence(params.pop("_run_id", None))
    thread_id = str(params.get("thread_id") or "")
    yield sequence.stamp(
        protocol_event(
            "lifecycle",
            {
                "event": "started",
                "run_id": sequence.run_id,
                "thread_id": thread_id,
                "graph_name": params.get("agent_name", "default"),
            },
        )
    )
    interrupted = False
    run_fn = run_agent_edit_stream if edit else run_agent_stream
    params["_protocol_events"] = True
    params["emit_thinking"] = True
    try:
        async with aclosing(run_fn(**params)) as events:
            async for event in events:
                if event.get("type") == "protocol_event":
                    item = event["event"]
                    if (
                        item["method"] == "custom:k41"
                        and item["params"]["data"].get("type") == "run_state"
                    ):
                        interrupted = item["params"]["data"].get("interrupted", False)
                    yield sequence.stamp(item)
                else:
                    yield sequence.stamp(
                        protocol_event("custom:k41", {**event, "projection": "ui"})
                    )
        yield sequence.stamp(
            protocol_event(
                "lifecycle",
                {
                    "event": "interrupted" if interrupted else "completed",
                    "run_id": sequence.run_id,
                    "thread_id": thread_id,
                },
            )
        )
    except Exception as exc:
        error = classify_agent_error(exc)
        yield sequence.stamp(
            protocol_event(
                "lifecycle",
                {
                    "event": "failed",
                    "error": error.message,
                    "code": error.code,
                    "run_id": sequence.run_id,
                    "thread_id": thread_id,
                },
            )
        )
        raise


async def run_agent_edit_protocol_stream(**params):
    async with aclosing(run_agent_protocol_stream(edit=True, **params)) as events:
        async for event in events:
            yield event
