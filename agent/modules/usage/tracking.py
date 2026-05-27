from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler, BaseCallbackManager
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult

from agent.modules.usage.repository import UsageEventInput
from agent.modules.usage.service import get_usage_service, usage_context_from_config
from agent.shared.infrastructure.db.base import utcnow

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UsageTrackingInfo:
    agent_name: str
    provider_name: str
    model_name: str
    call_kind: str = "agent"
    internal: bool = False


@dataclass(frozen=True, slots=True)
class ExtractedUsage:
    has_usage_metadata: bool
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    input_token_details: dict[str, Any] | None
    output_token_details: dict[str, Any] | None
    usage_metadata: dict[str, Any] | None


class LLMUsageCallback(BaseCallbackHandler):
    def __init__(
        self,
        *,
        config: dict[str, Any] | None,
        tracking: UsageTrackingInfo,
    ) -> None:
        super().__init__()
        self._context = usage_context_from_config(config)
        self._tracking = tracking

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        usage = extract_usage(response)
        event = UsageEventInput(
            thread_id=self._context.thread_id,
            root_thread_id=self._context.root_thread_id,
            platform=self._context.platform,
            user_id=self._context.user_id,
            channel_id=self._context.channel_id,
            agent_name=self._tracking.agent_name,
            provider_name=self._tracking.provider_name,
            model_name=self._tracking.model_name,
            call_kind=self._tracking.call_kind,
            internal=self._tracking.internal,
            has_usage_metadata=usage.has_usage_metadata,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            input_token_details=usage.input_token_details,
            output_token_details=usage.output_token_details,
            usage_metadata=usage.usage_metadata,
            run_id=str(run_id),
            parent_run_id=str(parent_run_id or ""),
            created_at=utcnow(),
        )
        _schedule_record(event)


def with_usage_tracking(
    config: dict[str, Any] | None,
    *,
    agent_name: str,
    provider_name: str,
    model_name: str,
    call_kind: str = "agent",
    internal: bool = False,
) -> dict[str, Any]:
    next_config = dict(config or {})
    handler = LLMUsageCallback(
        config=next_config,
        tracking=UsageTrackingInfo(
            agent_name=agent_name,
            provider_name=provider_name,
            model_name=model_name,
            call_kind=call_kind,
            internal=internal,
        )
    )
    next_config["callbacks"] = _append_callback(next_config.get("callbacks"), handler)
    return next_config


def _append_callback(callbacks: Any, handler: BaseCallbackHandler) -> Any:
    if callbacks is None:
        return [handler]
    if isinstance(callbacks, BaseCallbackManager):
        manager = callbacks.copy()
        manager.add_handler(handler)
        return manager
    if isinstance(callbacks, BaseCallbackHandler):
        return [callbacks, handler]
    try:
        existing = list(callbacks)
    except TypeError:
        return [handler]
    existing.append(handler)
    return existing


def extract_usage(response: LLMResult) -> ExtractedUsage:
    usage_values: list[dict[str, Any]] = []
    for generations in response.generations or []:
        for generation in generations:
            message = getattr(generation, "message", None)
            if isinstance(message, AIMessage):
                usage = _usage_dict(getattr(message, "usage_metadata", None))
                if usage:
                    usage_values.append(usage)

    if usage_values:
        return _combine_usage(usage_values)

    llm_output = response.llm_output or {}
    token_usage = llm_output.get("token_usage") if isinstance(llm_output, dict) else None
    usage = _usage_dict(token_usage)
    if usage:
        return _usage_from_dict(usage)

    return ExtractedUsage(
        has_usage_metadata=False,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        input_token_details=None,
        output_token_details=None,
        usage_metadata=None,
    )


def _usage_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        dumped = dump()
        return dict(dumped) if isinstance(dumped, dict) else {}
    try:
        return dict(value)
    except (TypeError, ValueError):
        return {}


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _token_value(usage: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _int_or_none(usage.get(key))
        if value is not None:
            return value
    return None


def _usage_from_dict(usage: dict[str, Any]) -> ExtractedUsage:
    input_tokens = _token_value(usage, "input_tokens", "prompt_tokens")
    output_tokens = _token_value(usage, "output_tokens", "completion_tokens")
    total_tokens = _token_value(usage, "total_tokens")
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = int(input_tokens or 0) + int(output_tokens or 0)

    input_details = _usage_dict(usage.get("input_token_details")) or None
    output_details = _usage_dict(usage.get("output_token_details")) or None
    normalized = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    if input_details:
        normalized["input_token_details"] = input_details
    if output_details:
        normalized["output_token_details"] = output_details

    return ExtractedUsage(
        has_usage_metadata=True,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        input_token_details=input_details,
        output_token_details=output_details,
        usage_metadata={key: value for key, value in normalized.items() if value is not None},
    )


def _combine_usage(usages: list[dict[str, Any]]) -> ExtractedUsage:
    extracted = [_usage_from_dict(usage) for usage in usages]
    input_tokens = sum(item.input_tokens or 0 for item in extracted)
    output_tokens = sum(item.output_tokens or 0 for item in extracted)
    total_tokens = sum(item.total_tokens or 0 for item in extracted)
    input_details = _sum_details(item.input_token_details for item in extracted)
    output_details = _sum_details(item.output_token_details for item in extracted)
    usage_metadata: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    if input_details:
        usage_metadata["input_token_details"] = input_details
    if output_details:
        usage_metadata["output_token_details"] = output_details
    return ExtractedUsage(
        has_usage_metadata=True,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        input_token_details=input_details or None,
        output_token_details=output_details or None,
        usage_metadata=usage_metadata,
    )


def _sum_details(values: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        if not value:
            continue
        for key, raw in value.items():
            amount = _int_or_none(raw)
            if amount is not None:
                result[str(key)] = result.get(str(key), 0) + amount
    return result


def _schedule_record(event: UsageEventInput) -> None:
    async def record() -> None:
        await get_usage_service().record_event(event)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(record())
        except Exception as exc:
            logger.debug("Failed to record LLM usage event outside event loop: %s", exc)
        return

    loop.create_task(record())


__all__ = [
    "ExtractedUsage",
    "LLMUsageCallback",
    "UsageTrackingInfo",
    "extract_usage",
    "with_usage_tracking",
]
