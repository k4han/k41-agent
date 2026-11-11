"""Decorator-based registration for built-in tools.

Applying ``@register_tool`` attaches metadata to a ``BaseTool`` instance.
``BuiltinToolSource`` discovers those instances directly from their modules.

Tools live as the result of the LangChain ``@tool`` decorator (or
``StructuredTool.from_function``), so this decorator must operate on the
``BaseTool`` instance returned, not on the underlying function.
"""

from __future__ import annotations

from agent.modules.tools.permissions.risk import RiskLevel

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from langchain_core.tools import BaseTool

from agent.modules.tools.domain import (
    ToolCapability,
    ToolCategory,
    ToolConfigSchema,
    ToolConfigValue,
    ToolConfigNormalizer,
    ToolFactory,
)

META_ATTR = "__k41_tool_meta__"


@dataclass(frozen=True)
class ToolRegistration:
    """Metadata captured by ``@register_tool`` before a descriptor is built."""

    category: ToolCategory
    capabilities: frozenset[ToolCapability]
    tags: frozenset[str]
    explicit_id: str | None
    version: str
    config_schema: ToolConfigSchema | None
    default_config: dict[str, ToolConfigValue]
    factory: ToolFactory | None
    config_normalizer: ToolConfigNormalizer | None
    risk: RiskLevel | None = None
    self_authorizing: bool = False
    may_interrupt: bool | None = None
    prepare_permissions: Callable | None = None


def register_tool(
    *,
    category: ToolCategory,
    risk: RiskLevel | None = None,
    self_authorizing: bool = False,
    may_interrupt: bool | None = None,
    prepare_permissions: Callable | None = None,
    capabilities: Iterable[ToolCapability] = (),
    tags: Iterable[str] = (),
    id: str | None = None,
    version: str = "1.0.0",
    config_schema: ToolConfigSchema | None = None,
    default_config: dict[str, ToolConfigValue] | None = None,
    factory: ToolFactory | None = None,
    config_normalizer: ToolConfigNormalizer | None = None,
) -> Callable[[BaseTool], BaseTool]:
    """Mark a ``BaseTool`` instance for built-in registration.

    Apply the default middleware immediately so error normalization works
    regardless of whether the registry has been initialized.
    """

    def decorator(tool_obj: BaseTool) -> BaseTool:
        if not isinstance(tool_obj, BaseTool):
            raise TypeError(
                "register_tool must be applied to a BaseTool instance "
                "(the result of @tool or StructuredTool.from_function); "
                f"got {type(tool_obj).__name__!r} instead."
            )
        declared_capabilities = frozenset(capabilities)
        if risk == RiskLevel.SAFE and declared_capabilities.intersection({
            ToolCapability.WRITE_FS, ToolCapability.EXEC_SHELL, ToolCapability.MUTATES_STATE,
        }):
            raise ValueError(f"Safe tool {tool_obj.name!r} declares mutating capabilities.")
        meta = ToolRegistration(
            category=category, risk=risk, self_authorizing=self_authorizing,
            may_interrupt=may_interrupt, prepare_permissions=prepare_permissions,
            capabilities=declared_capabilities,
            tags=frozenset(tags),
            explicit_id=id,
            version=version,
            config_schema=config_schema,
            default_config=dict(default_config or {}),
            factory=factory,
            config_normalizer=config_normalizer,
        )
        setattr(tool_obj, META_ATTR, meta)
        # local import to avoid circular dependency at module-import time
        from agent.modules.tools.middleware import apply_default_middleware

        apply_default_middleware(tool_obj)
        return tool_obj

    return decorator


__all__ = [
    "META_ATTR",
    "ToolRegistration",
    "register_tool",
]
