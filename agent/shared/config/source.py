from __future__ import annotations

from typing import Any, Protocol


class ConfigSource(Protocol):
    """Protocol for configuration sources.

    A config source provides configuration values from a specific backend
    (e.g., YAML file, database, defaults). Sources are ordered by priority,
    with higher priority sources overriding lower priority ones.
    """

    def get(self, key: str) -> Any | None:
        """Get a single config value by key.

        Args:
            key: Config key in dot notation (e.g., "llm.api_key")

        Returns:
            Config value or None if not found
        """
        ...

    def get_all(self) -> dict[str, Any]:
        """Get all config values from this source.

        Returns:
            Dictionary of all config key-value pairs
        """
        ...

    def reload(self) -> None:
        """Reload config from source.

        For file-based sources, this clears cache and re-reads the file.
        For in-memory sources, this is typically a no-op.
        """
        ...

    @property
    def priority(self) -> int:
        """Source priority for conflict resolution.

        Higher priority sources override lower priority ones.
        Typical values:
        - 0: Default/hardcoded values
        - 100: File-based config
        - 200: Database config
        """
        ...


__all__ = ["ConfigSource"]
