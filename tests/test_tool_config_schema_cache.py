"""Serialized schemas follow the container-owned tool registry."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from langchain_core.tools import tool

from agent.modules.tools import ToolCategory, ToolConfigField, ToolConfigSchema, ToolDescriptor, ToolSource
from agent.modules.tools import config as tool_config
from agent.modules.tools.registry_service import ToolRegistryService


@tool
def sample_tool(text: str) -> str:
    """Return sample text."""
    return text


def descriptor(default: str) -> ToolDescriptor:
    return ToolDescriptor(
        id="builtin.utility.sample_tool", name="sample_tool", description="Sample tool",
        source=ToolSource.BUILTIN, category=ToolCategory.UTILITY, tool=sample_tool,
        config_schema=ToolConfigSchema(fields=(
            ToolConfigField(name="mode", input_type="select", label="Mode", options=("first", "second")),
        )),
        default_config={"mode": default},
    )


def test_serialized_schemas_are_cached_and_caller_mutations_are_isolated(monkeypatch):
    serialize = Mock(wraps=tool_config.serialize_tool_config_schemas)
    monkeypatch.setattr(tool_config, "serialize_tool_config_schemas", serialize)
    registry = ToolRegistryService()
    registry.load_descriptors([descriptor("first")])
    first = registry.get_builtin_config_schemas()
    first["sample_tool"]["fields"][0]["options"].append("changed")
    first["sample_tool"]["default_config"]["mode"] = "changed"

    second = registry.get_builtin_config_schemas()
    assert second["sample_tool"]["fields"][0]["options"] == ["first", "second"]
    assert second["sample_tool"]["default_config"]["mode"] == "first"
    assert serialize.call_count == 1

    other = ToolRegistryService()
    other.load_descriptors([descriptor("second")])
    assert other.get_builtin_config_schemas()["sample_tool"]["default_config"]["mode"] == "second"
    assert registry.get_builtin_config_schemas()["sample_tool"]["default_config"]["mode"] == "first"
    assert serialize.call_count == 2


@pytest.mark.parametrize("mutation", ["load", "remove", "replace"])
def test_builtin_catalog_changes_refresh_serialized_schemas(mutation):
    registry = ToolRegistryService()
    registry.load_descriptors([descriptor("first")])
    registry.get_builtin_config_schemas()

    if mutation == "load":
        registry.load_descriptors([replace(descriptor("second"), id="builtin.utility.other", name="other")])
        assert registry.get_builtin_config_schemas()["other"]["default_config"]["mode"] == "second"
    elif mutation == "remove":
        registry.remove_by_source(ToolSource.BUILTIN)
        assert registry.get_builtin_config_schemas() == {}
    else:
        registry.replace_source(ToolSource.BUILTIN, [descriptor("second")])
        assert registry.get_builtin_config_schemas()["sample_tool"]["default_config"]["mode"] == "second"


def test_mcp_catalog_updates_preserve_cached_builtin_schemas(monkeypatch):
    registry = ToolRegistryService()
    registry.load_descriptors([descriptor("first")])
    expected = registry.get_builtin_config_schemas()
    serialize = Mock(wraps=tool_config.serialize_tool_config_schemas)
    monkeypatch.setattr(tool_config, "serialize_tool_config_schemas", serialize)

    registry.replace_source(ToolSource.MCP, [])
    assert registry.get_builtin_config_schemas() == expected
    serialize.assert_not_called()
