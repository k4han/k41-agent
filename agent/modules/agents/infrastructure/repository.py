"""Filesystem repository for scanning and caching agent configs."""

from __future__ import annotations

import logging
from pathlib import Path

from agent.modules.agents.domain.subagent import AgentConfig
from agent.modules.agents.infrastructure.parser import parse_agent_file

logger = logging.getLogger(__name__)


DEFAULT_AGENTS_DIR = str(Path.home() / ".kaka-agent" / "agents")
LEGACY_SUBAGENTS_DIR = str(Path.home() / ".kaka-agent" / "subagents")


def _get_builtin_default_agent() -> AgentConfig:
    """Return built-in default agent config as fallback."""
    return AgentConfig(
        name="default",
        display_name="Default Assistant",
        description="General-purpose AI assistant",
        graph_type="react_agent",
        service_type="default",
        model="devstral-2512",
        tools=[],  # Empty = use all default tools
        max_context_tokens=50_000,
        system_prompt="You are a helpful AI assistant.\nWorking directory: {working_dir}",
    )


class FilesystemAgentRepository:
    """Scans a directory for *.md files, parses them, and caches AgentConfig."""

    def __init__(self, dir_path: str | Path | None = None):
        self._dir = Path(dir_path) if dir_path else Path(DEFAULT_AGENTS_DIR)
        self._cache: dict[str, AgentConfig] = {}

    def _scan_dirs(self) -> list[Path]:
        """Return directories to scan for agent markdown files.

        By default, support both the new agents dir and the legacy subagents dir.
        If a custom dir is provided, only that directory is scanned.
        """
        default_dir = Path(DEFAULT_AGENTS_DIR)
        if self._dir != default_dir:
            return [self._dir]

        legacy_dir = Path(LEGACY_SUBAGENTS_DIR)
        if legacy_dir == default_dir:
            return [default_dir]
        return [default_dir, legacy_dir]

    def load(self) -> dict[str, AgentConfig]:
        """Scan directory, parse all *.md, cache and return dict by name."""
        agents: dict[str, AgentConfig] = {}
        loaded_from: dict[str, Path] = {}

        for scan_dir in self._scan_dirs():
            if not scan_dir.is_dir():
                logger.debug("Agents directory %s does not exist, skipping.", scan_dir)
                continue

            for md_path in sorted(scan_dir.glob("*.md")):
                if not md_path.is_file():
                    continue
                config = parse_agent_file(md_path)
                if config is None:
                    continue
                if config.name in agents:
                    logger.warning(
                        "Duplicate agent name '%s' in %s (already loaded from %s) — skipping duplicate.",
                        config.name,
                        md_path,
                        loaded_from[config.name],
                    )
                    continue
                agents[config.name] = config
                loaded_from[config.name] = md_path

        # Ensure default agent always exists
        if "default" not in agents:
            agents["default"] = _get_builtin_default_agent()
            logger.debug("Using built-in default agent (no default.md found)")

        self._cache = agents
        count = len(agents)
        scan_dirs = ", ".join(str(p) for p in self._scan_dirs())
        if count:
            logger.info("Loaded %d agent(s) from %s", count, scan_dirs)
        else:
            logger.debug("No agent files found in %s", scan_dirs)
        return agents

    def reload(self) -> dict[str, AgentConfig]:
        """Clear cache and re-scan the filesystem."""
        self._cache = {}
        return self.load()

    def get_cached(self) -> dict[str, AgentConfig]:
        """Return current cache. Call load() first if you need refresh."""
        return dict(self._cache)


# --- Module-level singleton ---

_repository: FilesystemAgentRepository | None = None


def get_repository() -> FilesystemAgentRepository:
    global _repository
    if _repository is None:
        # Singleton repository always uses default scan directories.
        # For one-off custom scans, call load_agents_from_dir(dir_path).
        _repository = FilesystemAgentRepository(None)
    return _repository


def load_agents_from_dir(dir_path: str | Path | None = None) -> dict[str, AgentConfig]:
    """Convenience: get repo singleton, scan, return dict."""
    repo = get_repository()
    if dir_path is not None:
        repo = FilesystemAgentRepository(dir_path)
    return repo.load()


def reload_agents() -> dict[str, AgentConfig]:
    """Convenience: reload from filesystem."""
    return get_repository().reload()
