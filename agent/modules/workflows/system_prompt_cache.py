"""TTL + revision cache for the assembled llm_node system prompt.

``llm_node`` runs on every LLM turn and the system prompt it needs is almost
always byte-identical to the previous turn. Rebuilding it costs one database
round-trip for prompt variables plus a workspace tree walk and N file reads for
repository-local skills, which is expensive on sandbox backends.

Cache keys embed both the structural inputs and the revision counters of the
mutable sources, so a dashboard edit is picked up on the next turn. The TTL is
an upper bound for changes this process cannot observe, most notably a prompt
variable written by another worker: revision counters are process-local, so a
multi-worker deployment relies on the TTL alone. It is therefore kept just
above the typical gap between two LLM turns rather than as long as possible.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Sequence
from typing import Any

from agent.shared.infrastructure.revisions import (
    AGENTS_REVISION,
    PROMPT_VARIABLES_REVISION,
    SKILLS_REVISION,
    get_revision,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_CACHE_TTL_KEY = "prompt_cache.ttl_seconds"
DEFAULT_SYSTEM_PROMPT_CACHE_TTL_SECONDS = 20
MAX_SYSTEM_PROMPT_CACHE_ENTRIES = 128

SystemPromptCacheKey = tuple[object, ...]

_lock = threading.Lock()
_entries: OrderedDict[SystemPromptCacheKey, tuple[float, str]] = OrderedDict()


def _now() -> float:
    return time.monotonic()


def get_system_prompt_cache_ttl_seconds() -> int:
    """Return the configured TTL. Values ``<= 0`` disable the cache."""
    from agent.shared.config.service import get_config_service

    return get_config_service().get_int(
        SYSTEM_PROMPT_CACHE_TTL_KEY,
        DEFAULT_SYSTEM_PROMPT_CACHE_TTL_SECONDS,
    )


def build_system_prompt_cache_key(
    *,
    agent_name: str,
    working_dir: str,
    workspace_label: str,
    tool_names: Sequence[str],
    allowed_skill_names: Sequence[str] | None,
    thread_id: str | None,
    workspace: Any = None,
    repository_skill_dir: str = "",
) -> SystemPromptCacheKey:
    """Build a cache key covering every input that shapes the system prompt.

    ``provider`` and ``model`` are deliberately excluded: they select the chat
    model but never change the prompt text. The agent card body is covered by
    ``agent_name`` plus the agents revision.
    """
    return (
        str(agent_name or ""),
        str(working_dir or ""),
        str(workspace_label or ""),
        tuple(sorted(str(name or "") for name in tool_names)),
        None
        if allowed_skill_names is None
        else tuple(sorted(str(name or "") for name in allowed_skill_names)),
        str(thread_id or ""),
        None if workspace is None else (
            str(workspace.backend),
            str(workspace.locator),
            json.dumps(workspace.metadata, sort_keys=True, default=str),
        ),
        str(repository_skill_dir),
        get_revision(AGENTS_REVISION),
        get_revision(SKILLS_REVISION),
        get_revision(PROMPT_VARIABLES_REVISION),
    )


def get_cached_system_prompt(key: SystemPromptCacheKey) -> str | None:
    """Return a cached prompt for ``key`` when present and not expired."""
    if get_system_prompt_cache_ttl_seconds() <= 0:
        return None

    now = _now()
    with _lock:
        entry = _entries.get(key)
        if entry is None:
            return None
        expires_at, prompt = entry
        if now >= expires_at:
            del _entries[key]
            return None
        _entries.move_to_end(key)
        return prompt


def _drop_expired_entries(now: float) -> None:
    """Remove every expired entry. Caller must hold ``_lock``."""
    expired_keys = [
        entry_key
        for entry_key, (expires_at, _) in _entries.items()
        if now >= expires_at
    ]
    for entry_key in expired_keys:
        del _entries[entry_key]


def store_system_prompt(key: SystemPromptCacheKey, prompt: str) -> None:
    """Store ``prompt`` under ``key``, evicting the least recently used entry.

    Expired entries are swept on insert so they never occupy LRU slots;
    otherwise a recently touched but expired entry could outlive the cap
    eviction and push out a still-valid entry.
    """
    ttl_seconds = get_system_prompt_cache_ttl_seconds()
    if ttl_seconds <= 0:
        return

    now = _now()
    expires_at = now + ttl_seconds
    with _lock:
        _entries[key] = (expires_at, prompt)
        _entries.move_to_end(key)
        _drop_expired_entries(now)
        while len(_entries) > MAX_SYSTEM_PROMPT_CACHE_ENTRIES:
            _entries.popitem(last=False)


def invalidate_system_prompt_cache() -> None:
    """Drop every cached system prompt."""
    with _lock:
        _entries.clear()


__all__ = [
    "DEFAULT_SYSTEM_PROMPT_CACHE_TTL_SECONDS",
    "MAX_SYSTEM_PROMPT_CACHE_ENTRIES",
    "SYSTEM_PROMPT_CACHE_TTL_KEY",
    "SystemPromptCacheKey",
    "build_system_prompt_cache_key",
    "get_cached_system_prompt",
    "get_system_prompt_cache_ttl_seconds",
    "invalidate_system_prompt_cache",
    "store_system_prompt",
]
