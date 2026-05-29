"""Tests for ToolRegistry, register_tool decorator, and BuiltinToolSource."""

from __future__ import annotations

import pytest
from langchain_core.tools import BaseTool, tool

from agent.modules.tools import (
    ToolCapability,
    ToolCategory,
    ToolDescriptor,
    ToolSource,
    find_descriptors,
    find_tools,
    get_default_descriptors,
    get_default_tool_names,
    get_default_tools,
    get_tool_by_name,
)
from agent.modules.tools.decorators import (
    PendingToolMeta,
    clear_pending_registrations,
    get_pending_registrations,
    register_tool,
)
from agent.modules.tools.registry import ToolRegistry
from agent.modules.tools.sources.builtin import BuiltinToolSource


@pytest.fixture
def empty_registry() -> ToolRegistry:
    return ToolRegistry()


@tool
def _sample_a(x: str) -> str:
    """Sample A."""
    return x


@tool
def _sample_b(x: str) -> str:
    """Sample B."""
    return x


def _make_descriptor(
    tool_obj: BaseTool,
    *,
    id: str,
    category: ToolCategory = ToolCategory.UTILITY,
    source: ToolSource = ToolSource.BUILTIN,
    capabilities: frozenset[ToolCapability] = frozenset(),
    tags: frozenset[str] = frozenset(),
) -> ToolDescriptor:
    return ToolDescriptor(
        id=id,
        name=tool_obj.name,
        description=tool_obj.description,
        source=source,
        category=category,
        tool=tool_obj,
        capabilities=capabilities,
        tags=tags,
    )


class TestToolRegistry:
    def test_add_and_get_by_id_and_name(self, empty_registry: ToolRegistry) -> None:
        desc = _make_descriptor(_sample_a, id="builtin.utility._sample_a")
        empty_registry.add(desc)
        assert empty_registry.get("builtin.utility._sample_a") is desc
        assert empty_registry.get(_sample_a.name) is desc
        assert empty_registry.get_tool(_sample_a.name) is _sample_a

    def test_duplicate_id_raises(self, empty_registry: ToolRegistry) -> None:
        empty_registry.add(_make_descriptor(_sample_a, id="x"))
        with pytest.raises(ValueError):
            empty_registry.add(_make_descriptor(_sample_b, id="x"))

    def test_remove(self, empty_registry: ToolRegistry) -> None:
        desc = _make_descriptor(_sample_a, id="builtin.x.a")
        empty_registry.add(desc)
        empty_registry.remove(desc.name)
        assert empty_registry.get(desc.name) is None
        assert empty_registry.get(desc.id) is None

    def test_contains_and_len(self, empty_registry: ToolRegistry) -> None:
        empty_registry.add(_make_descriptor(_sample_a, id="i1"))
        empty_registry.add(_make_descriptor(_sample_b, id="i2"))
        assert len(empty_registry) == 2
        assert "i1" in empty_registry
        assert _sample_a.name in empty_registry
        assert "missing" not in empty_registry

    def test_resolve_preserves_input_order_and_skips_unknown(
        self, empty_registry: ToolRegistry
    ) -> None:
        empty_registry.add(_make_descriptor(_sample_a, id="a"))
        empty_registry.add(_make_descriptor(_sample_b, id="b"))
        resolved = empty_registry.resolve([_sample_b.name, "unknown", _sample_a.name])
        assert resolved == [_sample_b, _sample_a]

    def test_find_by_category_and_capability(self, empty_registry: ToolRegistry) -> None:
        empty_registry.add(
            _make_descriptor(
                _sample_a,
                id="file.a",
                category=ToolCategory.FILE,
                capabilities=frozenset({ToolCapability.READ_FS}),
                tags=frozenset({"io"}),
            )
        )
        empty_registry.add(
            _make_descriptor(
                _sample_b,
                id="web.b",
                category=ToolCategory.WEB,
                capabilities=frozenset({ToolCapability.NETWORK}),
                tags=frozenset({"http"}),
            )
        )

        files = empty_registry.find(category=ToolCategory.FILE)
        assert [d.id for d in files] == ["file.a"]

        reads = empty_registry.find(capabilities=[ToolCapability.READ_FS])
        assert [d.id for d in reads] == ["file.a"]

        any_caps = empty_registry.find(
            any_capabilities=[ToolCapability.NETWORK, ToolCapability.EXEC_SHELL]
        )
        assert [d.id for d in any_caps] == ["web.b"]

        tagged = empty_registry.find(tags=["http"])
        assert [d.id for d in tagged] == ["web.b"]

        none_match = empty_registry.find(
            category=ToolCategory.WEB, capabilities=[ToolCapability.READ_FS]
        )
        assert none_match == []


class TestRegisterToolDecorator:
    def test_decorator_attaches_meta_and_records(self) -> None:
        clear_pending_registrations()

        @register_tool(
            category=ToolCategory.UTILITY,
            capabilities=[ToolCapability.MUTATES_STATE],
            tags=["x"],
        )
        @tool
        def my_local_tool(text: str) -> str:
            """local."""
            return text

        meta = getattr(my_local_tool, "__kaka_tool_meta__", None)
        assert isinstance(meta, PendingToolMeta)
        assert meta.category is ToolCategory.UTILITY
        assert ToolCapability.MUTATES_STATE in meta.capabilities
        assert "x" in meta.tags

        pending = get_pending_registrations()
        names = [t.name for t, _ in pending]
        assert "my_local_tool" in names

    def test_decorator_rejects_non_basetool(self) -> None:
        with pytest.raises(TypeError):

            @register_tool(category=ToolCategory.UTILITY)
            def not_a_tool(x: int) -> int:  # type: ignore[misc]
                return x


class TestBuiltinToolSource:
    def test_loads_known_builtin_tools(self) -> None:
        descriptors = BuiltinToolSource().load()
        ids = {d.id for d in descriptors}
        names = {d.name for d in descriptors}
        expected_names = {
            "read_file",
            "write_file",
            "list_files",
            "bash",
            "bash_send_input",
            "bash_interrupt",
            "skill",
            "echo",
            "get_current_time",
            "write_todos",
            "call_agent",
            "schedule_task",
            "list_scheduled_tasks",
            "delete_scheduled_task",
            "web_search",
            "web_fetch",
        }
        assert expected_names.issubset(names)
        assert all(id_.startswith("builtin.") for id_ in ids)

    def test_descriptors_have_correct_categories(self) -> None:
        by_name = {d.name: d for d in BuiltinToolSource().load()}
        assert by_name["read_file"].category is ToolCategory.FILE
        assert by_name["bash"].category is ToolCategory.SHELL
        assert by_name["web_fetch"].category is ToolCategory.WEB
        assert by_name["call_agent"].category is ToolCategory.AGENT
        assert by_name["schedule_task"].category is ToolCategory.SCHEDULE
        assert by_name["skill"].category is ToolCategory.SKILL

    def test_descriptors_include_capabilities(self) -> None:
        by_name = {d.name: d for d in BuiltinToolSource().load()}
        assert ToolCapability.READ_FS in by_name["read_file"].capabilities
        assert ToolCapability.WRITE_FS in by_name["write_file"].capabilities
        assert ToolCapability.EXEC_SHELL in by_name["bash"].capabilities
        assert ToolCapability.NETWORK in by_name["web_search"].capabilities
        assert ToolCapability.MUTATES_STATE in by_name["write_todos"].capabilities

    def test_load_is_idempotent_in_id_space(self) -> None:
        source = BuiltinToolSource()
        first = {d.id for d in source.load()}
        second = {d.id for d in source.load()}
        assert first == second


class TestPublicFacade:
    def test_get_default_tools_returns_builtin_tools(self) -> None:
        tools = get_default_tools()
        names = {t.name for t in tools}
        assert "read_file" in names
        assert "web_search" in names

    def test_get_default_descriptors(self) -> None:
        descriptors = get_default_descriptors()
        assert all(isinstance(d, ToolDescriptor) for d in descriptors)
        assert len(descriptors) >= 14

    def test_get_default_tool_names_matches_tools(self) -> None:
        names = set(get_default_tool_names())
        assert {"read_file", "write_file", "bash", "web_fetch"}.issubset(names)

    def test_get_tool_by_name_known(self) -> None:
        t = get_tool_by_name("read_file")
        assert t is not None
        assert t.name == "read_file"

    def test_get_tool_by_name_unknown(self) -> None:
        assert get_tool_by_name("does_not_exist") is None

    def test_find_tools_by_category(self) -> None:
        file_tools = find_tools(category=ToolCategory.FILE)
        names = {t.name for t in file_tools}
        assert {"read_file", "write_file", "list_files"}.issubset(names)

    def test_find_descriptors_by_capability(self) -> None:
        net = find_descriptors(capabilities=[ToolCapability.NETWORK])
        names = {d.name for d in net}
        assert {"web_fetch", "web_search"}.issubset(names)
