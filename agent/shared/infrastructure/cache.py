from datetime import datetime, timedelta, timezone
from typing import Any, Optional


class InMemoryCache:
    """Simple in-memory cache with TTL support."""

    def __init__(self):
        self._cache: dict[str, tuple[Any, datetime]] = {}

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired."""
        if key not in self._cache:
            return None

        value, expires_at = self._cache[key]
        if datetime.now(timezone.utc) > expires_at:
            del self._cache[key]
            return None

        return value

    def set(self, key: str, value: Any, ttl_seconds: int = 300):
        """Set value in cache with TTL (default 5 minutes)."""
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        self._cache[key] = (value, expires_at)

    def delete(self, key: str):
        """Delete key from cache."""
        self._cache.pop(key, None)

    def clear(self):
        """Clear all cache entries."""
        self._cache.clear()


def get_cache(container=None) -> InMemoryCache:
    """Return container-scoped cache."""
    from agent.bootstrap.container import require_active_container

    return require_active_container(container).cache
