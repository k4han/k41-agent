"""Process-local revision counters used to invalidate derived caches.

Callers bump a namespace whenever the underlying source of truth changes
(agent cards, skills, prompt variables). Consumers embed the current revision
in their own cache keys so an in-process mutation is reflected immediately
without them having to subscribe to every mutation site.

This module lives in ``shared`` so low-level modules can bump revisions
without importing the higher-level packages that consume them.
"""

from __future__ import annotations

import threading

AGENTS_REVISION = "agents"
SKILLS_REVISION = "skills"
PROMPT_VARIABLES_REVISION = "prompt_variables"

_lock = threading.Lock()
_revisions: dict[str, int] = {}


def bump_revision(namespace: str) -> int:
    """Increment and return the revision counter for ``namespace``."""
    with _lock:
        current = _revisions.get(namespace, 0) + 1
        _revisions[namespace] = current
        return current


def get_revision(namespace: str) -> int:
    """Return the current revision counter for ``namespace``."""
    with _lock:
        return _revisions.get(namespace, 0)


def reset_revisions() -> None:
    """Clear all revision counters."""
    with _lock:
        _revisions.clear()


__all__ = [
    "AGENTS_REVISION",
    "PROMPT_VARIABLES_REVISION",
    "SKILLS_REVISION",
    "bump_revision",
    "get_revision",
    "reset_revisions",
]
