"""Built-in tool source.

Walks the configured tool packages, imports every submodule (so that any
``@register_tool`` decorators run), then converts their attached metadata
into ``ToolDescriptor`` instances.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Iterable

from langchain_core.tools import BaseTool

from agent.modules.tools.decorators import (
    META_ATTR,
    ToolRegistration,
)
from agent.modules.tools.domain import (
    ToolCategory,
    ToolDescriptor,
    ToolSource,
)

logger = logging.getLogger(__name__)

DEFAULT_BUILTIN_PACKAGES: tuple[str, ...] = (
    "agent.modules.tools.builtin",
)


class BuiltinToolSource:
    """Adapter that produces descriptors from decorator-registered tools."""

    name = "builtin"

    def __init__(self, packages: Iterable[str] = DEFAULT_BUILTIN_PACKAGES) -> None:
        self._packages = tuple(packages)
        self._descriptors: list[ToolDescriptor] | None = None

    def load(self) -> list[ToolDescriptor]:
        if self._descriptors is None:
            tagged = self._discover_tagged_tools()
            self._descriptors = self._build_descriptors(tagged)
        return list(self._descriptors)

    async def aload(self) -> list[ToolDescriptor]:
        """Expose the shared asynchronous source interface."""
        return self.load()

    def reload(self) -> list[ToolDescriptor]:
        self._descriptors = None
        return self.load()

    def _discover_tagged_tools(
        self,
    ) -> list[tuple[BaseTool, ToolRegistration]]:
        """Walk configured packages and collect every BaseTool that was
        marked by ``@register_tool``.

        Module attributes remain discoverable even when imports are cached.
        """
        found: dict[int, tuple[BaseTool, ToolRegistration]] = {}
        for pkg_name in self._packages:
            try:
                pkg = importlib.import_module(pkg_name)
            except ImportError:
                logger.warning("Cannot import tool package %s", pkg_name)
                continue
            pkg_path = getattr(pkg, "__path__", None)
            if pkg_path is None:
                continue
            for _, modname, _ in pkgutil.walk_packages(pkg_path, prefix=pkg.__name__ + "."):
                try:
                    module = importlib.import_module(modname)
                except ImportError as exc:
                    logger.warning("Failed to import tool module %s: %s", modname, exc)
                    continue
                for attr_name in dir(module):
                    obj = getattr(module, attr_name, None)
                    if not isinstance(obj, BaseTool):
                        continue
                    meta = getattr(obj, META_ATTR, None)
                    if not isinstance(meta, ToolRegistration):
                        continue
                    found.setdefault(id(obj), (obj, meta))
        return list(found.values())

    @staticmethod
    def _build_descriptors(
        pending: Iterable[tuple[BaseTool, ToolRegistration]],
    ) -> list[ToolDescriptor]:
        out: list[ToolDescriptor] = []
        seen_ids: set[str] = set()
        for tool_obj, meta in pending:
            tool_name = getattr(tool_obj, "name", None) or str(tool_obj)
            category: ToolCategory = meta.category
            desc_id = meta.explicit_id or (
                f"{ToolSource.BUILTIN.value}.{category.value}.{tool_name}"
            )
            if desc_id in seen_ids:
                logger.debug("Skipping duplicate tool id %s", desc_id)
                continue
            seen_ids.add(desc_id)
            out.append(
                ToolDescriptor(
                    id=desc_id,
                    name=tool_name,
                    description=getattr(tool_obj, "description", "") or "",
                    source=ToolSource.BUILTIN,
                    category=category,
                    tool=tool_obj,
                    capabilities=meta.capabilities,
                    tags=meta.tags,
                    version=meta.version,
                    args_schema=getattr(tool_obj, "args_schema", None),
                    config_schema=meta.config_schema,
                    default_config=meta.default_config,
                    factory=meta.factory,
                    config_normalizer=meta.config_normalizer,
                )
            )
        return out


__all__ = ["BuiltinToolSource", "DEFAULT_BUILTIN_PACKAGES"]
