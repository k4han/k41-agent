"""Connection validation owned by the web tool integration."""

from __future__ import annotations

import logging
from functools import partial

from agent.shared.config.web_connections import (
    DEFAULT_CONNECTION,
    MIGRATION_KEY,
    WEB_SERVICES,
    WEB_TOOLS,
    is_legacy_web_key,
    validate_tool_references,
)

logger = logging.getLogger(__name__)


def normalize_web_config(values, overrides, service, *, tool):
    """Validate selected connections while allowing unused providers to fail."""
    values = dict(values)
    if service.get(MIGRATION_KEY):
        for field in list(values):
            if field not in overrides and is_legacy_web_key(f"tools.{tool}.{field}"):
                values.pop(field)
    provider = str(values.get("provider") or "auto").strip().lower() or "auto"
    for field in list(values):
        if not field.endswith("_connection"):
            continue
        try:
            validate_tool_references(service, tool, {field: values[field]})
        except ValueError:
            kind = field.removesuffix("_connection")
            if provider == kind and provider in WEB_SERVICES and WEB_TOOLS[tool] in WEB_SERVICES[provider]["capabilities"]:
                raise
            logger.warning("Ignoring invalid %s connection for %s", kind, tool)
            values[field] = DEFAULT_CONNECTION
    return values


normalize_search_config = partial(normalize_web_config, tool="web_search")
normalize_fetch_config = partial(normalize_web_config, tool="web_fetch")
