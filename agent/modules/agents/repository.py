"""Filesystem repository for scanning and caching agent configs."""

from __future__ import annotations

import logging
from pathlib import Path

from agent.modules.agents.models import AgentCard, AgentConfig
from agent.modules.agents.parser import parse_agent_file_with_error, serialize_agent_config

logger = logging.getLogger(__name__)


DEFAULT_AGENTS_DIR = str(Path.home() / ".kaka-agent" / "agents")

# Directory containing agents shipped with the package (bundled defaults).
_BUILTIN_DIR = Path(__file__).parent / "_builtin"


def _load_builtin_agent_cards() -> list[AgentCard]:
    """Load agent card metadata from the bundled _builtin directory.

    Invalid files are returned as invalid cards so the dashboard can surface
    parse errors instead of silently hiding them.
    """
    if not _BUILTIN_DIR.is_dir():
        logger.debug("Builtin agents directory %s not found — skipping.", _BUILTIN_DIR)
        return []

    cards: list[AgentCard] = []
    for md_path in sorted(_BUILTIN_DIR.glob("*.md")):
        if not md_path.is_file():
            continue
        config, error = parse_agent_file_with_error(md_path)
        if config is None:
            logger.warning("Skipping invalid builtin agent file %s: %s", md_path, error)
            cards.append(
                AgentCard.invalid(
                    name=md_path.stem,
                    source="builtin",
                    path=str(md_path),
                    editable=False,
                    error=error,
                )
            )
            continue
        cards.append(
            AgentCard.from_config(
                config,
                source="builtin",
                path=str(md_path),
                editable=False,
            )
        )
        logger.debug("Loaded builtin agent '%s' from %s", config.name, md_path)
    return cards


def _load_builtin_agents() -> dict[str, AgentConfig]:
    """Load all valid agent MD files from the bundled _builtin directory."""
    return {
        card.name: card.to_agent_config()
        for card in _load_builtin_agent_cards()
        if card.valid
    }


class FilesystemAgentRepository:
    """Scans a directory for *.md files, parses them, and caches AgentConfig."""

    def __init__(self, dir_path: str | Path | None = None):
        self._dir = Path(dir_path) if dir_path else Path(DEFAULT_AGENTS_DIR)
        self._cache: dict[str, AgentConfig] = {}
        self._card_cache: list[AgentCard] = []
        self._user_paths: dict[str, Path] = {}

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
        builtin_cards = _load_builtin_agent_cards()
        agents: dict[str, AgentConfig] = {
            card.name: card.to_agent_config()
            for card in builtin_cards
            if card.valid
        }
        builtin_names = set(agents)
        cards_by_name: dict[str, AgentCard] = {
            card.name: card
            for card in builtin_cards
            if card.valid
        }
        invalid_cards = [card for card in builtin_cards if not card.valid]
        loaded_from: dict[str, Path] = {}  # tracks user-dir file paths for dup warnings
        user_paths: dict[str, Path] = {}

        # 2. Load user-defined agents — they override builtins of the same name.
        for scan_dir in self._scan_dirs():
            if not scan_dir.is_dir():
                logger.debug("Agents directory %s does not exist, skipping.", scan_dir)
                continue

            for md_path in sorted(scan_dir.glob("*.md")):
                if not md_path.is_file():
                    continue
                config, error = parse_agent_file_with_error(md_path)
                if config is None:
                    invalid_cards.append(
                        AgentCard.invalid(
                            name=md_path.stem,
                            source="user",
                            path=str(md_path),
                            editable=True,
                            error=error,
                        )
                    )
                    continue
                if config.name in loaded_from:
                    logger.warning(
                        "Duplicate agent name '%s' in %s (already loaded from %s) — skipping duplicate.",
                        config.name,
                        md_path,
                        loaded_from[config.name],
                    )
                    invalid_cards.append(
                        AgentCard.invalid(
                            name=config.name,
                            source="user",
                            path=str(md_path),
                            editable=True,
                            error=(
                                "Duplicate agent name already loaded from "
                                f"{loaded_from[config.name]}."
                            ),
                        )
                    )
                    continue
                if config.name in agents:
                    logger.info(
                        "User agent '%s' (%s) overrides builtin.",
                        config.name,
                        md_path,
                    )
                agents[config.name] = config
                cards_by_name[config.name] = AgentCard.from_config(
                    config,
                    source="user",
                    path=str(md_path),
                    editable=True,
                    overrides_builtin=config.name in builtin_names,
                )
                loaded_from[config.name] = md_path
                user_paths[config.name] = md_path

        self._cache = agents
        self._user_paths = user_paths
        self._card_cache = sorted(
            cards_by_name.values(),
            key=lambda card: (card.name.lower(), card.source),
        ) + sorted(
            invalid_cards,
            key=lambda card: (card.name.lower(), card.path),
        )
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
        self._card_cache = []
        self._user_paths = {}
        return self.load()

    def get_cached(self) -> dict[str, AgentConfig]:
        """Return current cache. Call load() first if you need refresh."""
        return dict(self._cache)

    def list_cards(self) -> list[AgentCard]:
        """Return all effective agent cards with file metadata."""
        if not self._card_cache:
            self.load()
        return list(self._card_cache)

    def get_card(self, name: str) -> AgentCard | None:
        """Return a dashboard card by agent name."""
        for card in self.list_cards():
            if card.name == name and card.valid:
                return card
        return None

    def get_builtin_card(self, name: str) -> AgentCard | None:
        """Return a builtin card by agent name, regardless of user overrides."""
        for card in _load_builtin_agent_cards():
            if card.name == name and card.valid:
                return card
        return None

    @property
    def user_dir(self) -> Path:
        """Directory where user-editable agent cards are stored."""
        return self._dir

    def _ensure_user_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _canonical_user_path(self, name: str) -> Path:
        return self._dir / f"{name}.md"

    def get_user_path(self, name: str) -> Path | None:
        """Return the existing user card path for a loaded agent name."""
        if not self._user_paths:
            self.load()
        return self._user_paths.get(name)

    def create_user_agent(self, config: AgentConfig) -> AgentCard:
        """Create a new user agent card using the canonical file name."""
        self._ensure_user_dir()
        if self.get_user_path(config.name) is not None:
            raise FileExistsError(f"Agent '{config.name}' already has a user card.")

        path = self._canonical_user_path(config.name)
        if path.exists():
            raise FileExistsError(f"Agent file already exists: {path}")

        path.write_text(serialize_agent_config(config), encoding="utf-8")
        self.reload()
        card = self.get_card(config.name)
        if card is None:
            raise RuntimeError(f"Agent '{config.name}' was written but could not be reloaded.")
        return card

    def update_user_agent(self, name: str, config: AgentConfig) -> AgentCard:
        """Update an existing user agent card."""
        if config.name != name:
            raise ValueError("Agent name cannot be changed.")

        self._ensure_user_dir()
        path = self.get_user_path(name) or self._canonical_user_path(name)
        if not path.exists():
            raise FileNotFoundError(f"User agent '{name}' does not exist.")

        path.write_text(serialize_agent_config(config), encoding="utf-8")
        self.reload()
        card = self.get_card(config.name)
        if card is None:
            raise RuntimeError(f"Agent '{config.name}' was written but could not be reloaded.")
        return card

    def delete_user_agent(self, name: str) -> None:
        """Delete a user agent card."""
        path = self.get_user_path(name) or self._canonical_user_path(name)
        if not path.exists():
            raise FileNotFoundError(f"User agent '{name}' does not exist.")

        path.unlink()
        self.reload()

    def clone_builtin_agent(self, name: str) -> AgentCard:
        """Clone a builtin card into the user agents directory."""
        builtin_card = self.get_builtin_card(name)
        if builtin_card is None:
            raise FileNotFoundError(f"Builtin agent '{name}' does not exist.")
        if self.get_user_path(name) is not None:
            raise FileExistsError(f"Agent '{name}' already has a user override.")

        return self.create_user_agent(builtin_card.to_agent_config())


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
