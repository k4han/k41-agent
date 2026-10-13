"""Retain large tool text in real workspace files before checkpointing."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from types import SimpleNamespace

from langchain_core.messages import ToolMessage
from langgraph.types import Command

from agent.modules.tools.coding.adapter import invocation_context
from agent.modules.tools.coding.models import ToolResult
from agent.modules.tools.coding.service import get_coding_service
from agent.modules.tools.coding.storage import MAX_STORED_BYTES, bounded_text

logger = logging.getLogger(__name__)


async def retain_message(message: ToolMessage, runtime) -> ToolMessage:
    artifact = message.artifact if isinstance(message.artifact, dict) else {}
    blocks = message.content if isinstance(message.content, list) else None
    text = message.content if blocks is None else "\n".join(
        block if isinstance(block, str) else block.get("text", "")
        for block in blocks if isinstance(block, str) or block.get("type") == "text"
    )
    if not bounded_text(text)[1]:
        return message
    normalized_content = message.content if blocks is None else [
        {"type": "text", "text": block} if isinstance(block, str) else block for block in blocks
    ]
    result = ToolResult(content=normalized_content)
    try:
        context = invocation_context(SimpleNamespace(context=runtime.context, config=runtime.config,
                                                    tool_call_id=message.tool_call_id))
        service = get_coding_service()
        if context.backend == "local":
            result = await asyncio.to_thread(service.storage.bound, result, context)
        else:
            # Bound the RPC payload as well as the bytes retained inside the sandbox.
            if len(text.encode("utf-8")) > MAX_STORED_BYTES:
                kept = text.encode("utf-8")[:MAX_STORED_BYTES].decode("utf-8", errors="ignore")
                result.content = kept if blocks is None else [
                    {"type": "text", "text": kept},
                    *(block for block in blocks if isinstance(block, dict) and block.get("type") != "text"),
                ]
                result.capture_truncated = True
            result = await service.remote.retain(result, context)
    except Exception as exc:
        logger.warning("Failed to retain tool output: %s", exc)
        preview, _ = bounded_text(text, "[output truncated; failed to retain output; some output was lost]")
        result.content = preview if blocks is None else [
            {"type": "text", "text": preview},
            *(block for block in blocks if isinstance(block, dict) and block.get("type") != "text"),
        ]
        result.capture_truncated = result.output_truncated = True
        result.warnings.append("Output retention failed.")
    metadata = {"output_paths": result.output_paths, "output_truncated": result.output_truncated,
                "capture_truncated": result.capture_truncated, "warnings": result.warnings}
    return message.model_copy(update={
        "content": result.content,
        "additional_kwargs": {**message.additional_kwargs, "output_retention": metadata},
        "artifact": {**artifact, **metadata} if isinstance(message.artifact, dict) else message.artifact,
    })


async def retain_tool_messages(value, runtime):
    """Preserve ToolNode command updates, message identities, and error statuses."""
    if isinstance(value, ToolMessage):
        return await retain_message(value, runtime)
    if isinstance(value, Command):
        return replace(value, update=await retain_tool_messages(value.update, runtime))
    if isinstance(value, dict):
        return {**value, "messages": await retain_tool_messages(value["messages"], runtime)} if "messages" in value else value
    if isinstance(value, list):
        return await asyncio.gather(*(retain_tool_messages(item, runtime) for item in value))
    return value
