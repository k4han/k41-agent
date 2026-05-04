"""Filesystem repository for scanning and caching agent configs."""

from __future__ import annotations

import logging
from pathlib import Path

from agent.modules.agents.models import AgentConfig
from agent.modules.agents.parser import parse_agent_file

logger = logging.getLogger(__name__)


DEFAULT_AGENTS_DIR = str(Path.home() / ".kaka-agent" / "agents")

# Directory containing agents shipped with the package (bundled defaults).
_BUILTIN_DIR = Path(__file__).parent / "_builtin"


def _load_builtin_agents() -> dict[str, AgentConfig]:
    """Load all agent MD files from the bundled _builtin directory.

    Returns a dict keyed by agent name. On parse failure the file is skipped
    and a warning is logged. If the builtin directory does not exist, an empty
    dict is returned and a debug message is logged.
    """
    if not _BUILTIN_DIR.is_dir():
        logger.debug("Builtin agents directory %s not found — skipping.", _BUILTIN_DIR)
        return {}

    agents: dict[str, AgentConfig] = {}
    for md_path in sorted(_BUILTIN_DIR.glob("*.md")):
        if not md_path.is_file():
            continue
        config = parse_agent_file(md_path)
        if config is None:
            continue
        agents[config.name] = config
        logger.debug("Loaded builtin agent '%s' from %s", config.name, md_path)
    return agents


class FilesystemAgentRepository:
    """Scans a directory for *.md files, parses them, and caches AgentConfig."""

    def __init__(self, dir_path: str | Path | None = None):
        self._dir = Path(dir_path) if dir_path else Path(DEFAULT_AGENTS_DIR)
        self._cache: dict[str, AgentConfig] = {}

    def _scan_dirs(self) -> list[Path]:
        """Return directories to scan for agent markdown files.

        If a custom dir is provided, only that directory is scanned.
        """
        return [self._dir]

    def load(self) -> dict[str, AgentConfig]:
        """Scan directories and return all agents, with user agents overriding builtins.

        Load order (later entries override earlier ones on name collision):
        1. Bundled agents from the package _builtin directory.
        2. User-defined agents from ~/.kaka-agent/agents.

        This means a user can shadow any builtin agent (including "default") by
        creating a file with the same ``name`` field in their agents directory.
        """
        # 1. Start with bundled agents.
        agents: dict[str, AgentConfig] = _load_builtin_agents()
        loaded_from: dict[str, Path] = {}  # tracks user-dir file paths for dup warnings

        # 2. Load user-defined agents — they override builtins of the same name.
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
                if config.name in loaded_from:
                    logger.warning(
                        "Duplicate agent name '%s' in %s (already loaded from %s) — skipping duplicate.",
                        config.name,
                        md_path,
                        loaded_from[config.name],
                    )
                    continue
                if config.name in agents:
                    logger.info(
                        "User agent '%s' (%s) overrides builtin.",
                        config.name,
                        md_path,
                    )
                agents[config.name] = config
                loaded_from[config.name] = md_path

        self._cache = agents
        count = len(agents)
        scan_dirs = ", ".join(str(p) for p in self._scan_dirs())
        if count:
            logger.info("Loaded %d agent(s) total (%s + builtins)", count, scan_dirs)
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
