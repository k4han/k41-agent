import threading
import time
from collections import OrderedDict
from typing import Any, Optional


class InMemoryCache:
    """Thread-safe TTL/LRU cache with at most 1024 entries by default.

    One reusable worker cleans up expired entries every 60 seconds by default
    and stops when the cache becomes empty.
    """

    def __init__(self, cleanup_interval_seconds: float = 60, *, maxsize: int = 1024):
        if cleanup_interval_seconds <= 0:
            raise ValueError("Cleanup interval must be positive.")
        if maxsize <= 0:
            raise ValueError("Cache maxsize must be positive.")
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._cleanup_interval_seconds = cleanup_interval_seconds
        self._cleanup_thread: threading.Thread | None = None
        self._cleanup_stop_event: threading.Event | None = None

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
            self._cache.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl_seconds: float = 300):
        """Set value in cache with TTL (default 5 minutes)."""
        with self._lock:
            if ttl_seconds <= 0:
                self._cache.pop(key, None)
                self._cancel_cleanup_if_empty()
                return
            now = time.monotonic()
            if key not in self._cache and len(self._cache) >= self._maxsize:
                self._expire_entries(now)
                if len(self._cache) >= self._maxsize:
                    self._cache.popitem(last=False)
            self._cache[key] = (value, now + ttl_seconds)
            self._cache.move_to_end(key)
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
        """Start one worker while entries exist; callers hold the lock."""
        if self._cache and self._cleanup_thread is None:
            stop_event = threading.Event()
            worker = threading.Thread(
                target=self._expire,
                args=(stop_event,),
                name="in-memory-cache-cleanup",
                daemon=True,
            )
            self._cleanup_stop_event = stop_event
            self._cleanup_thread = worker
            worker.start()

    def _cancel_cleanup_if_empty(self) -> None:
        if not self._cache and self._cleanup_stop_event is not None:
            self._cleanup_stop_event.set()
            self._cleanup_stop_event = None
            self._cleanup_thread = None

    def _expire_entries(self, now: float) -> None:
        """Remove expired entries; callers hold the lock."""
        expired = [key for key, (_, expires_at) in self._cache.items() if expires_at <= now]
        for key in expired:
            del self._cache[key]

    def _expire(self, stop_event: threading.Event) -> None:
        while not stop_event.wait(self._cleanup_interval_seconds):
            with self._lock:
                # A cancelled worker may already be waiting for this lock.
                if stop_event is not self._cleanup_stop_event:
                    return
                self._expire_entries(time.monotonic())
                self._cancel_cleanup_if_empty()


def get_cache(container=None) -> InMemoryCache:
    """Return container-scoped cache."""
    from agent.bootstrap.container import require_active_container

    return require_active_container(container).cache
