import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
                if not cache._cache and cache._cleanup_thread is None:
                    break
            threading.Event().wait(0.01)
        with cache._lock:
            assert cache._cache == {}
            assert cache._cleanup_thread is None
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
        assert cache._cleanup_thread is None
    finally:
        cache.clear()


@pytest.mark.parametrize("ttl", [0, -1])
def test_nonpositive_ttl_removes_previous_value(ttl):
    cache = InMemoryCache()
    cache.set("key", "old")
    cache.set("key", "new", ttl_seconds=ttl)
    assert cache.get("key") is None
    assert cache._cache == {}
    assert cache._cleanup_thread is None


@pytest.mark.parametrize("operation", ["clear", "delete"])
def test_removing_last_entry_cancels_cleanup_and_allows_reuse(operation):
    cache = InMemoryCache()
    try:
        cache.set("key", "value")
        previous_thread = cache._cleanup_thread
        if operation == "clear":
            cache.clear()
        else:
            cache.delete("key")
        assert cache._cleanup_thread is None
        previous_thread.join(timeout=2)
        assert not previous_thread.is_alive()
        cache.set("other", "replacement")
        assert cache.get("other") == "replacement"
        assert cache._cleanup_thread is not previous_thread
    finally:
        cache.clear()


def test_default_capacity_is_bounded():
    cache = InMemoryCache()
    try:
        for index in range(1025):
            cache.set(str(index), index)
        assert len(cache._cache) == 1024
        assert cache.get("0") is None
        assert cache.get("1024") == 1024
    finally:
        cache.clear()


@pytest.mark.parametrize("maxsize", [0, -1])
def test_nonpositive_maxsize_is_rejected(maxsize):
    with pytest.raises(ValueError, match="maxsize must be positive"):
        InMemoryCache(maxsize=maxsize)


@pytest.mark.parametrize("operation", ["get", "set"])
def test_capacity_evicts_least_recently_used_entry(operation):
    cache = InMemoryCache(maxsize=2)
    try:
        cache.set("first", "original")
        cache.set("second", "other")
        if operation == "get":
            assert cache.get("first") == "original"
        else:
            cache.set("first", "replacement")
        cache.set("third", "new")
        assert cache.get("second") is None
        assert cache.get("first") == ("original" if operation == "get" else "replacement")
        assert cache.get("third") == "new"
        assert len(cache._cache) == 2
    finally:
        cache.clear()


def test_overwriting_at_capacity_does_not_evict_another_entry():
    cache = InMemoryCache(maxsize=2)
    try:
        cache.set("first", "original")
        cache.set("second", "other")
        cache.set("first", "replacement")
        assert cache.get("first") == "replacement"
        assert cache.get("second") == "other"
    finally:
        cache.clear()


def test_expired_entries_are_removed_before_evicting_live_entries(monkeypatch):
    now = 10.0
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(monotonic=lambda: now))
    cache = InMemoryCache(maxsize=2)
    try:
        cache.set("live", "keep", ttl_seconds=10)
        cache.set("expired", "discard", ttl_seconds=1)
        now = 11.0
        cache.set("new", "value")
        assert cache.get("live") == "keep"
        assert cache.get("expired") is None
        assert cache.get("new") == "value"
        assert len(cache._cache) == 2
    finally:
        cache.clear()


def test_periodic_cleanup_reuses_one_thread(monkeypatch):
    sweep_threads = []
    sweeps_completed = threading.Event()
    main_thread = threading.current_thread()

    def record_monotonic():
        current = threading.current_thread()
        if current is not main_thread:
            sweep_threads.append(current)
            if len(sweep_threads) >= 3:
                sweeps_completed.set()
        return time.monotonic()

    monkeypatch.setattr(cache_module, "time", SimpleNamespace(monotonic=record_monotonic))
    cache = InMemoryCache(cleanup_interval_seconds=0.01)
    assert cache._cleanup_thread is None
    try:
        cache.set("key", "value")
        assert sweeps_completed.wait(timeout=2)
        assert len(set(sweep_threads)) == 1
        assert cache.get("key") == "value"
    finally:
        cache.clear()
        for worker in set(sweep_threads):
            worker.join(timeout=2)
            assert not worker.is_alive()


def test_concurrent_access_respects_capacity():
    cache = InMemoryCache(maxsize=8)

    def access_entries(worker_id):
        for index in range(50):
            key = f"{worker_id}:{index}"
            cache.set(key, index)
            assert cache.get(key) in (None, index)
            if index % 2 == 0:
                cache.delete(key)
            with cache._lock:
                assert len(cache._cache) <= 8

    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(access_entries, range(4)))
    finally:
        cache.clear()
