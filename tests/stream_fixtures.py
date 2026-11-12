"""Scripted v3 graph runs for application-level runner tests."""

from contextlib import aclosing

from langchain_core.messages import AIMessageChunk

from agent.shared.infrastructure.parsing import extract_message_thinking
from agent.shared.infrastructure.thinking_parser import strip_thinking_tags


def graph_event(method, data):
    return {
        "type": "event",
        "method": method,
        "params": {
            "namespace": [],
            "timestamp": 0,
            "data": data,
        },
    }


class EventGraph:
    """Implement the v3 run contract using a scripted sequence of graph updates."""

    async def astream_events(self, payload, **kwargs):
        assert kwargs["version"] == "v3"
        return EventRun(self.events(payload, **kwargs))


class EventRun:
    def __init__(self, events):
        self.events = events
        self.was_interrupted = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.events.aclose()

    async def __aiter__(self):
        async with aclosing(self.events):
            async for event in self.events:
                if isinstance(event, tuple):
                    mode, data = event[-2:]
                else:
                    mode, data = "values", event
                if mode == "messages":
                    chunk, metadata = data
                    assert isinstance(chunk, AIMessageChunk)
                    metadata = {
                        **metadata,
                        "run_id": metadata.get("run_id") or chunk.id or "model",
                    }
                    thinking = extract_message_thinking(chunk)
                    if thinking:
                        yield graph_event(
                            "messages",
                            [
                                {
                                    "event": "content-block-delta",
                                    "index": 0,
                                    "delta": {
                                        "type": "reasoning-delta",
                                        "reasoning": thinking,
                                    },
                                },
                                metadata,
                            ],
                        )
                    text = (
                        strip_thinking_tags(chunk.content)
                        if isinstance(chunk.content, str)
                        else "".join(
                            block.get("text", "")
                            for block in chunk.content
                            if isinstance(block, dict) and block.get("type") != "thinking"
                        )
                    )
                    if text:
                        yield graph_event(
                            "messages",
                            [
                                {
                                    "event": "content-block-delta",
                                    "index": 1,
                                    "delta": {"type": "text-delta", "text": text},
                                },
                                metadata,
                            ],
                        )
                else:
                    self.was_interrupted |= bool(
                        isinstance(data, dict) and data.get("__interrupt__")
                    )
                    yield graph_event(mode, data)

    async def output(self):
        return {}

    async def interrupted(self):
        return self.was_interrupted
