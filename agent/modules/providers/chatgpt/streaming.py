"""Normalize completed function calls before LangChain merges Responses events."""

from __future__ import annotations

from typing import Any


class _FunctionCallEvents:
    def __init__(self) -> None:
        self.calls: dict[str, dict[str, Any]] = {}
        self.emitted: set[str] = set()

    def _complete(self, event: Any, item: Any = None) -> list[Any]:
        item = item if item is not None else event.item
        if item.id in self.emitted:
            return []
        self.emitted.add(item.id)
        call = self.calls.pop(item.id, None)
        # One complete added event avoids partial JSON, duplicate arguments and
        # the upstream positional merger splitting interleaved tool calls.
        header = call["header"] if call else event
        return [header.model_copy(update={"type": "response.output_item.added", "item": item})]

    def convert(self, event: Any) -> list[Any]:
        kind = event.type
        item = getattr(event, "item", None)
        if kind == "response.output_item.added" and item.type == "function_call":
            self.calls[item.id] = {"header": event, "arguments": item.arguments or ""}
            return []
        if kind == "response.function_call_arguments.delta":
            if call := self.calls.get(event.item_id):
                call["arguments"] += event.delta
            return []
        if kind == "response.function_call_arguments.done":
            if call := self.calls.get(event.item_id):
                call["arguments"] = event.arguments
            return []
        if kind == "response.output_item.done" and item.type == "function_call":
            return self._complete(event)
        if kind in {"response.completed", "response.incomplete"}:
            result = []
            for index, output in enumerate(event.response.output or []):
                if output.type == "function_call":
                    result.extend(self._complete(event.model_copy(update={"output_index": index}), output))
            for call in list(self.calls.values()):
                header = call["header"]
                completed = header.item.model_copy(update={"arguments": call["arguments"]})
                result.extend(self._complete(header, completed))
            return [*result, event]
        return [event]


def _normalize_stream(stream: Any, *, asynchronous: bool) -> Any:
    """Wrap only this response's iterator; never mutate a shared SDK client."""
    events = _FunctionCallEvents()
    original = stream._iterator

    def iterate():
        for event in original:
            yield from events.convert(event)

    async def aiterate():
        async for event in original:
            for normalized in events.convert(event):
                yield normalized

    stream._iterator = aiterate() if asynchronous else iterate()
    return stream


class _RawResponse:
    def __init__(self, response: Any, *, asynchronous: bool) -> None:
        self.response = response
        self.asynchronous = asynchronous

    def __getattr__(self, name: str) -> Any:
        return getattr(self.response, name)

    def parse(self) -> Any:
        return _normalize_stream(self.response.parse(), asynchronous=self.asynchronous)


class ResponsesClient:
    """Request-local SDK view preserving headers, callbacks and stream cleanup."""

    def __init__(self, client: Any, *, asynchronous: bool = False, raw: bool = False) -> None:
        self.client = client
        self.asynchronous = asynchronous
        self.raw = raw

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)

    @property
    def with_raw_response(self) -> ResponsesClient:
        return ResponsesClient(self.client.with_raw_response, asynchronous=self.asynchronous, raw=True)

    @property
    def responses(self) -> ResponsesClient:
        return self

    def _wrap(self, response: Any) -> Any:
        if self.raw:
            return _RawResponse(response, asynchronous=self.asynchronous)
        return _normalize_stream(response, asynchronous=self.asynchronous)

    def create(self, *args: Any, **kwargs: Any) -> Any:
        if self.asynchronous:
            return self._acreate(*args, **kwargs)
        return self._wrap(self.client.responses.create(*args, **kwargs))

    async def _acreate(self, *args: Any, **kwargs: Any) -> Any:
        return self._wrap(await self.client.responses.create(*args, **kwargs))
