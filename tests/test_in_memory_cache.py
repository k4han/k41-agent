import threading
import time
from types import SimpleNamespace

import pytest

import agent.shared.infrastructure.cache as cache_module
from agent.shared.infrastructure.cache import InMemoryCache


def test_expired_entry_is_reclaimed_without_reading_it():
    cache = InMemoryCache(cleanup_interval_seconds=0.01)
    try:
        cache.set("transient", object(), ttl_seconds=0.02)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with cache._lock:
                if not cache._cache and cache._cleanup_timer is None:
                    break
            threading.Event().wait(0.01)
        with cache._lock:
            assert cache._cache == {}
            assert cache._cleanup_timer is None
    finally:
        cache.clear()


def test_overwrite_survives_original_expiry(monkeypatch):
    now = 10.0
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(monotonic=lambda: now))
    cache = InMemoryCache()
    try:
        cache.set("key", "original", ttl_seconds=1)
        now = 10.5
        cache.set("key", "replacement", ttl_seconds=5)
        now = 11.0
        assert cache.get("key") == "replacement"
        now = 15.5
        assert cache.get("key") is None
        assert cache._cleanup_timer is None
    finally:
        cache.clear()


@pytest.mark.parametrize("ttl", [0, -1])
def test_nonpositive_ttl_removes_previous_value(ttl):
    cache = InMemoryCache()
    cache.set("key", "old")
    cache.set("key", "new", ttl_seconds=ttl)
    assert cache.get("key") is None
    assert cache._cache == {}
    assert cache._cleanup_timer is None


@pytest.mark.parametrize("operation", ["clear", "delete"])
def test_removing_last_entry_cancels_cleanup_and_allows_reuse(operation):
    cache = InMemoryCache()
    try:
        cache.set("key", "value")
        previous_timer = cache._cleanup_timer
        if operation == "clear":
            cache.clear()
        else:
            cache.delete("key")
        assert cache._cleanup_timer is None
        assert previous_timer.finished.is_set()
        cache.set("other", "replacement")
        assert cache.get("other") == "replacement"
        assert cache._cleanup_timer is not previous_timer
    finally:
        cache.clear()
