"""Internal provider extensions loader.

Provides a decoupled discovery mechanism for optional, private or local
provider implementations without hardcoding proprietary logic into the
core codebase.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.modules.providers.service import ProviderService

logger = logging.getLogger(__name__)

_INTERNAL_MODULE_CANDIDATES = (
    "agent.internal.providers",
)


def load_internal_providers(service: ProviderService, container: Any = None) -> None:
    """Discover and initialize internal provider extensions if present.

    Gracefully succeeds as a no-op if no internal provider module is present.
    """
    for module_name in _INTERNAL_MODULE_CANDIDATES:
        try:
            mod = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            # Silently pass if root or intermediate internal module is not found.
            # A missing dependency *inside* the internal module must warn.
            missing_module = exc.name
            if missing_module is None:
                continue
            if (
                missing_module == module_name
                or module_name.startswith(missing_module + ".")
                or missing_module in ("agent.internal", "agent.internal.providers")
            ):
                continue
            logger.warning(
                "Internal provider module '%s' could not be imported: %s",
                module_name,
                exc,
            )
            continue
        except Exception as exc:
            logger.warning(
                "Failed to import internal provider module '%s': %s",
                module_name,
                exc,
            )
            continue

        register_fn = getattr(mod, "register", None)
        if not callable(register_fn):
            logger.debug(
                "Internal provider module '%s' has no register() function, skipping.",
                module_name,
            )
            continue
        try:
            register_fn(service, container=container)
            logger.debug(
                "Successfully registered internal provider module: %s",
                module_name,
            )
        except Exception as exc:
            logger.error(
                "Error executing register() in internal provider module '%s': %s",
                module_name,
                exc,
            )
