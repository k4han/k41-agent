"""Container isolation: no shared state across AppContainer instances."""

import threading
import time

import pytest

from agent.bootstrap.container import (
    clear_active_container,
    create_test_container,
    get_active_container,
)
from agent.shared.config import get_config_service


def test_containers_own_isolated_services(tmp_path):
    first = create_test_container(tmp_path=tmp_path / "first")
    second = create_test_container(tmp_path=tmp_path / "second")

    assert first.config_service is not second.config_service
    assert first.task_manager is not second.task_manager
    assert first.github_service is not second.github_service
    assert first.channel_manager is not second.channel_manager
    assert first.provider_service is not second.provider_service
    assert first.channel_registry is not second.channel_registry
    assert first.command_registry is not second.command_registry
    assert first.tool_registry_service is not second.tool_registry_service
    assert first.mcp_service is not second.mcp_service
    assert first.skill_repository is not second.skill_repository
    assert first.agent_catalog_service is not second.agent_catalog_service
    assert first.agent_repository is not second.agent_repository
    assert first.agent_catalog_service._repository is first.agent_repository
    assert second.agent_catalog_service._repository is second.agent_repository
    assert first.conversation_repository is not second.conversation_repository
    assert first.workspace_repository is not second.workspace_repository
    assert first.workspace_backend_registry is not second.workspace_backend_registry
    assert first.background_task_repository is not second.background_task_repository
    assert first.chat_stream_manager is not second.chat_stream_manager
    assert first.active_session_registry is not second.active_session_registry
    assert first.usage_service is not second.usage_service
    assert first.prompt_variable_service is not second.prompt_variable_service
    assert first.pairing_service is not second.pairing_service
    assert first.admin_auth_service is not second.admin_auth_service
    assert first.google_calendar_service is not second.google_calendar_service
    assert first.cache is not second.cache
    assert first.extension_registry is not second.extension_registry


def test_runtime_settings_are_live_not_snapshotted(tmp_path):
    container = create_test_container(tmp_path=tmp_path)
    first = container.runtime_settings
    second = container.runtime_settings

    assert first is not second
    assert first.channel_enabled == second.channel_enabled


def test_getters_resolve_active_container(tmp_path):
    from agent.modules.admin_auth.service import get_admin_auth_service
    from agent.modules.agent_runtime import get_background_task_manager
    from agent.modules.agent_runtime.repository import get_background_task_repository
    from agent.modules.agents.service import get_catalog_service
    from agent.modules.channels import get_channel_registry
    from agent.modules.channels.commands import get_default_command_registry
    from agent.modules.conversations.repository import get_conversation_thread_repository
    from agent.modules.prompt_variables.service import get_prompt_variable_service
    from agent.modules.tools.registry_service import get_registry_service
    from agent.modules.usage.service import get_usage_service
    from agent.modules.users.pairing import get_pairing_service
    from agent.shared.extension_registry import get_extension_registry
    from agent.shared.infrastructure.cache import get_cache

    container = create_test_container(tmp_path=tmp_path)
    previous = container.activate()
    try:
        assert get_config_service() is container.config_service
        assert get_active_container() is container
        assert get_background_task_manager() is container.task_manager
        assert get_background_task_repository() is container.background_task_repository
        assert get_catalog_service() is container.agent_catalog_service
        assert get_channel_registry() is container.channel_registry
        assert get_default_command_registry() is container.command_registry
        assert get_conversation_thread_repository() is container.conversation_repository
        assert get_prompt_variable_service() is container.prompt_variable_service
        assert get_registry_service() is container.tool_registry_service
        assert get_usage_service() is container.usage_service
        assert get_pairing_service() is container.pairing_service
        assert get_admin_auth_service() is container.admin_auth_service
        assert get_cache() is container.cache
        assert get_extension_registry() is container.extension_registry
    finally:
        container.deactivate(previous)
        clear_active_container()


def test_database_url_override_is_scoped(tmp_path):
    first = create_test_container(tmp_path=tmp_path / "a", database_url="sqlite+aiosqlite:///:memory:")
    second = create_test_container(tmp_path=tmp_path / "b", database_url="sqlite+aiosqlite:///:memory:")

    assert first.database_url == "sqlite+aiosqlite:///:memory:"
    assert second.database_url == "sqlite+aiosqlite:///:memory:"
    assert first._async_engine is None
    assert second._async_engine is None


def test_github_repository_store_is_container_scoped(tmp_path):
    first = create_test_container(tmp_path=tmp_path / "first")
    second = create_test_container(tmp_path=tmp_path / "second")

    assert first.github_repository_store is not second.github_repository_store
    assert first.github_service.store is first.github_repository_store
    assert second.github_service.store is second.github_repository_store


def test_get_github_repository_store_resolves_active_container(tmp_path):
    from agent.modules.github.repository import get_github_repository_store

    container = create_test_container(tmp_path=tmp_path)
    previous = container.activate()
    try:
        assert get_github_repository_store() is container.github_repository_store
    finally:
        container.deactivate(previous)
        clear_active_container()


@pytest.mark.asyncio
async def test_initialize_persistence_retry_reuses_existing_engine(tmp_path, monkeypatch):
    from agent.modules import usage as usage_module

    container = create_test_container(tmp_path=tmp_path)
    original_prune = usage_module.prune_usage_events

    async def failing_prune():
        raise RuntimeError("prune failed")

    monkeypatch.setattr(usage_module, "prune_usage_events", failing_prune)
    with pytest.raises(RuntimeError, match="prune failed"):
        await container.initialize_persistence()

    assert container._async_engine is not None
    assert container._persistence_ready is False
    engine_after_failure = container._async_engine

    monkeypatch.setattr(usage_module, "prune_usage_events", original_prune)
    try:
        await container.initialize_persistence()

        assert container._async_engine is engine_after_failure
        assert container._persistence_ready is True
        assert container._checkpointer is not None
    finally:
        await container.close_persistence()


@pytest.mark.asyncio
async def test_initialize_persistence_scopes_legacy_accessors_to_container(
    tmp_path, monkeypatch
):
    from agent.modules import usage as usage_module

    holder = create_test_container(tmp_path=tmp_path / "holder")
    container = create_test_container(tmp_path=tmp_path / "target")

    original_prune = usage_module.prune_usage_events
    observed: dict[str, object] = {}

    async def spy_prune():
        observed["active"] = get_active_container()
        await original_prune()

    monkeypatch.setattr(usage_module, "prune_usage_events", spy_prune)

    previous = holder.activate()
    try:
        await container.initialize_persistence()

        assert observed["active"] is container
        assert get_active_container() is holder
    finally:
        holder.deactivate(previous)
        clear_active_container()
        await container.close_persistence()


def test_lazy_property_initialization_is_thread_safe(tmp_path, monkeypatch):
    """Concurrent first access must build exactly one instance per container."""
    from agent.shared.infrastructure.cache import InMemoryCache

    created: list[InMemoryCache] = []

    class SlowCache(InMemoryCache):
        def __init__(self, *args, **kwargs):
            time.sleep(0.05)
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(
        "agent.shared.infrastructure.cache.InMemoryCache", SlowCache
    )

    container = create_test_container(tmp_path=tmp_path)
    assert container._cache is None

    thread_count = 4
    barrier = threading.Barrier(thread_count)
    results = []

    def worker():
        barrier.wait(timeout=5)
        results.append(container.cache)

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert container._cache is not None
    assert len(created) == 1
    assert len(results) == thread_count
    assert all(result is created[0] for result in results)


def test_nested_lazy_properties_share_reentrant_lock(tmp_path):
    """google_calendar_service resolves sibling lazy properties of itself."""
    container = create_test_container(tmp_path=tmp_path)

    service = container.google_calendar_service

    assert service is container._google_calendar_service
    assert service._client is container.google_calendar_client
    assert service._store is container.google_calendar_store
    assert service._oauth is container.google_oauth_manager
