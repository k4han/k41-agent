"""Coverage for the TTL + revision cache backing the llm_node system prompt."""

from __future__ import annotations

import pytest

from agent.modules.workflows import system_prompt_cache
from agent.shared.infrastructure.revisions import (
    AGENTS_REVISION,
    PROMPT_VARIABLES_REVISION,
    SKILLS_REVISION,
    bump_revision,
)


def _key(**overrides):
    kwargs = {
        "agent_name": "default",
        "working_dir": "D:/repo",
        "workspace_label": "owner/repo",
        "tool_names": ["read", "skill"],
        "allowed_skill_names": None,
        "thread_id": "thread-1",
    }
    kwargs.update(overrides)
    return system_prompt_cache.build_system_prompt_cache_key(**kwargs)


CONFIGURED_TTL_SECONDS = 30


@pytest.fixture(autouse=True)
def fixed_ttl(monkeypatch):
    """Pin a TTL that is deliberately not the default, to exercise the config path."""
    monkeypatch.setattr(
        system_prompt_cache,
        "get_system_prompt_cache_ttl_seconds",
        lambda: CONFIGURED_TTL_SECONDS,
    )


def test_identical_inputs_hit_the_cache() -> None:
    system_prompt_cache.store_system_prompt(_key(), "Prompt A")

    assert system_prompt_cache.get_cached_system_prompt(_key()) == "Prompt A"


def test_missing_key_returns_none() -> None:
    assert system_prompt_cache.get_cached_system_prompt(_key()) is None


def test_tool_name_order_does_not_change_the_key() -> None:
    system_prompt_cache.store_system_prompt(
        _key(tool_names=["read", "skill"]), "Prompt A"
    )

    cached = system_prompt_cache.get_cached_system_prompt(
        _key(tool_names=["skill", "read"])
    )

    assert cached == "Prompt A"


@pytest.mark.parametrize(
    "overrides",
    [
        {"agent_name": "other-agent"},
        {"working_dir": "D:/other"},
        {"workspace_label": "owner/other"},
        {"tool_names": ["read"]},
        {"allowed_skill_names": []},
        {"allowed_skill_names": ["repo-docs"]},
        {"thread_id": "thread-2"},
        {"repository_skill_dir": "custom/skills"},
    ],
)
def test_changed_inputs_miss_the_cache(overrides) -> None:
    system_prompt_cache.store_system_prompt(_key(), "Prompt A")

    assert system_prompt_cache.get_cached_system_prompt(_key(**overrides)) is None


def test_workspace_identity_separates_sandboxes_with_the_same_display_label() -> None:
    from agent.modules.workspaces import WorkspaceRef

    first = WorkspaceRef(backend="modal", locator="sandbox-a", metadata={"root": "/repo"})
    second = WorkspaceRef(backend="modal", locator="sandbox-b", metadata={"root": "/repo"})
    system_prompt_cache.store_system_prompt(_key(workspace=first), "First workspace")

    assert system_prompt_cache.get_cached_system_prompt(_key(workspace=second)) is None


@pytest.mark.parametrize(
    "namespace",
    [AGENTS_REVISION, SKILLS_REVISION, PROMPT_VARIABLES_REVISION],
)
def test_revision_bump_invalidates_the_key(namespace) -> None:
    system_prompt_cache.store_system_prompt(_key(), "Prompt A")

    bump_revision(namespace)

    assert system_prompt_cache.get_cached_system_prompt(_key()) is None


def test_entry_expires_after_ttl(monkeypatch) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr(system_prompt_cache, "_now", lambda: clock["now"])
    key = _key()
    system_prompt_cache.store_system_prompt(key, "Prompt A")

    clock["now"] += CONFIGURED_TTL_SECONDS - 1
    assert system_prompt_cache.get_cached_system_prompt(key) == "Prompt A"

    clock["now"] += 2.0
    assert system_prompt_cache.get_cached_system_prompt(key) is None


def test_cache_is_bounded_and_evicts_least_recently_used(monkeypatch) -> None:
    monkeypatch.setattr(system_prompt_cache, "MAX_SYSTEM_PROMPT_CACHE_ENTRIES", 2)
    first = _key(thread_id="thread-1")
    second = _key(thread_id="thread-2")
    third = _key(thread_id="thread-3")

    system_prompt_cache.store_system_prompt(first, "Prompt 1")
    system_prompt_cache.store_system_prompt(second, "Prompt 2")
    # Touch the first entry so the second one becomes least recently used.
    assert system_prompt_cache.get_cached_system_prompt(first) == "Prompt 1"
    system_prompt_cache.store_system_prompt(third, "Prompt 3")

    assert system_prompt_cache.get_cached_system_prompt(second) is None
    assert system_prompt_cache.get_cached_system_prompt(first) == "Prompt 1"
    assert system_prompt_cache.get_cached_system_prompt(third) == "Prompt 3"


def test_store_sweeps_expired_entries_before_enforcing_the_cap(monkeypatch) -> None:
    monkeypatch.setattr(system_prompt_cache, "MAX_SYSTEM_PROMPT_CACHE_ENTRIES", 2)
    clock = {"now": 1_000.0}
    monkeypatch.setattr(system_prompt_cache, "_now", lambda: clock["now"])
    first = _key(thread_id="thread-1")
    second = _key(thread_id="thread-2")
    third = _key(thread_id="thread-3")

    system_prompt_cache.store_system_prompt(first, "Prompt 1")
    clock["now"] += 1.0
    system_prompt_cache.store_system_prompt(second, "Prompt 2")
    # Touch the first entry so it becomes most recently used while keeping
    # its original expiry.
    assert system_prompt_cache.get_cached_system_prompt(first) == "Prompt 1"

    # The first entry is now expired but sits at the MRU end; inserting the
    # third entry must sweep it instead of evicting the still-valid second.
    clock["now"] = 1_000.0 + CONFIGURED_TTL_SECONDS + 0.5
    system_prompt_cache.store_system_prompt(third, "Prompt 3")

    assert system_prompt_cache.get_cached_system_prompt(first) is None
    assert system_prompt_cache.get_cached_system_prompt(second) == "Prompt 2"
    assert system_prompt_cache.get_cached_system_prompt(third) == "Prompt 3"


def test_non_positive_ttl_disables_the_cache(monkeypatch) -> None:
    monkeypatch.setattr(
        system_prompt_cache,
        "get_system_prompt_cache_ttl_seconds",
        lambda: 0,
    )
    key = _key()

    system_prompt_cache.store_system_prompt(key, "Prompt A")

    assert system_prompt_cache.get_cached_system_prompt(key) is None


def test_invalidate_drops_every_entry() -> None:
    system_prompt_cache.store_system_prompt(_key(thread_id="thread-1"), "Prompt 1")
    system_prompt_cache.store_system_prompt(_key(thread_id="thread-2"), "Prompt 2")

    system_prompt_cache.invalidate_system_prompt_cache()

    assert system_prompt_cache.get_cached_system_prompt(_key(thread_id="thread-1")) is None
    assert system_prompt_cache.get_cached_system_prompt(_key(thread_id="thread-2")) is None


def test_cache_hits_do_not_extend_the_ttl(monkeypatch) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr(system_prompt_cache, "_now", lambda: clock["now"])
    key = _key()
    system_prompt_cache.store_system_prompt(key, "Prompt A")

    for _ in range(3):
        clock["now"] += CONFIGURED_TTL_SECONDS / 4
        assert system_prompt_cache.get_cached_system_prompt(key) == "Prompt A"

    clock["now"] += CONFIGURED_TTL_SECONDS / 4
    assert system_prompt_cache.get_cached_system_prompt(key) is None


def test_ttl_defaults_when_config_key_is_absent() -> None:
    assert system_prompt_cache.DEFAULT_SYSTEM_PROMPT_CACHE_TTL_SECONDS == 20
    assert system_prompt_cache.SYSTEM_PROMPT_CACHE_TTL_KEY == "prompt_cache.ttl_seconds"
