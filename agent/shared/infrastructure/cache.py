import threading
import time
from typing import Any, Optional


class InMemoryCache:
    """Thread-safe TTL cache with automatic cleanup every 60 seconds by default."""

    def __init__(self, cleanup_interval_seconds: float = 60):
        if cleanup_interval_seconds <= 0:
            raise ValueError("Cleanup interval must be positive.")
        self._cache: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()
        self._cleanup_interval_seconds = cleanup_interval_seconds
        self._cleanup_timer: threading.Timer | None = None

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() >= expires_at:
                del self._cache[key]
                self._cancel_cleanup_if_empty()
                return None
            return value

    def set(self, key: str, value: Any, ttl_seconds: int = 300):
        """Set value in cache with TTL (default 5 minutes)."""
        with self._lock:
            if ttl_seconds <= 0:
                self._cache.pop(key, None)
                self._cancel_cleanup_if_empty()
                return
            self._cache[key] = (value, time.monotonic() + ttl_seconds)
            self._schedule_cleanup()

    def delete(self, key: str):
        """Delete key from cache."""
        with self._lock:
            self._cache.pop(key, None)
            self._cancel_cleanup_if_empty()

    def clear(self):
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()
            self._cancel_cleanup_if_empty()

    def _schedule_cleanup(self) -> None:
        """Schedule a sweep while entries exist; callers hold the lock."""
        if self._cache and self._cleanup_timer is None:
            timer = threading.Timer(self._cleanup_interval_seconds, self._expire)
            timer.daemon = True
            self._cleanup_timer = timer
            timer.start()

    def _cancel_cleanup_if_empty(self) -> None:
        if not self._cache and self._cleanup_timer is not None:
            self._cleanup_timer.cancel()
            self._cleanup_timer = None

    def _expire(self) -> None:
        with self._lock:
            # A cancelled sweep may already be waiting for this lock.
            if threading.current_thread() is not self._cleanup_timer:
                return
            self._cleanup_timer = None
            now = time.monotonic()
            expired = [key for key, (_, expires_at) in self._cache.items() if expires_at <= now]
            for key in expired:
                del self._cache[key]
            self._schedule_cleanup()


def get_cache(container=None) -> InMemoryCache:
    """Return container-scoped cache."""
    from agent.bootstrap.container import require_active_container

    return require_active_container(container).cache
