"""Retain large tool text in real workspace files before checkpointing."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import replace
from types import SimpleNamespace

from langchain_core.messages import ToolMessage
from langgraph.types import Command

from agent.modules.tools.coding.adapter import invocation_context
from agent.modules.tools.coding.models import ToolResult
from agent.modules.tools.coding.names import CODING_TOOLS
from agent.modules.tools.coding.service import get_coding_service
from agent.modules.tools.coding.storage import MAX_STORED_BYTES, RETENTION_SECONDS, bounded_text, output_relative_path

logger = logging.getLogger(__name__)


async def retain_message(message: ToolMessage, runtime) -> ToolMessage:
    if message.name in CODING_TOOLS:
        return message
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


async def migrate_history_outputs(messages, runtime):
    """Translate legacy output references without mutating stored checkpoints."""
    context = None
    migrated = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            migrated.append(message)
            continue
        artifact = message.artifact if isinstance(message.artifact, dict) else {}
        if not artifact and isinstance(message.content, str) and message.content.lstrip().startswith("{"):
            try:
                envelope = json.loads(message.content)
                if isinstance(envelope, dict) and {"status", "data", "output_refs"}.issubset(envelope):
                    artifact = envelope
                    message = message.model_copy(update={"content": envelope.get("content", message.content)})
            except ValueError:
                pass
        references = artifact.get("output_refs", []) if not artifact.get("output_paths") else []
        if isinstance(message.content, str):
            references = list(dict.fromkeys([*references, *re.findall(r"output_ref=([a-f0-9]{32})", message.content)]))
        paths = []
        for reference in references:
            if not isinstance(reference, str) or not re.fullmatch(r"[a-f0-9]{32}", reference):
                continue
            try:
                if context is None:
                    context = invocation_context(runtime)
                service = get_coding_service()
                if context.backend == "local":
                    await asyncio.to_thread(service.storage.output_path, context, reference)
                    paths.append(output_relative_path(context, reference))
                else:
                    # Host-side legacy diffs need uploading; sandbox process logs migrate in place.
                    legacy = service.storage.owner_dir(context) / f"{reference}.output"
                    if legacy.is_file() and not legacy.is_symlink() and legacy.stat().st_mtime >= time.time() - RETENTION_SECONDS:
                        content = await asyncio.to_thread(legacy.read_text, encoding="utf-8", errors="replace")
                        paths.extend(await service.remote.migrate_outputs([reference], context, legacy_content=content))
                    else:
                        paths.extend(await service.remote.migrate_outputs([reference], context))
            except Exception:
                continue
        if paths:
            notice = "\n[Retained output: " + "; ".join(f"read file_path={path} byte_offset=0" for path in paths) + "]"
            content = message.content + notice if isinstance(message.content, str) else [*message.content, {"type": "text", "text": notice}]
            if isinstance(content, str):
                content = re.sub(r"read_tool_output output_ref=[a-f0-9]{32}", "read the retained output file", content)
                content, _ = bounded_text(content, notice)
            message = message.model_copy(update={"content": content, "artifact": {**artifact, "output_paths": paths}})
        migrated.append(message)
    return migrated
