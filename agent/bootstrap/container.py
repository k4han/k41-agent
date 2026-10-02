"""Dependency injection container owning all shared resources.

Replaces module-level singletons with explicit construction. One
AppContainer instance owns ConfigService, database engine, LangGraph
checkpointer, channel manager, and background services for a single
process lifetime (web server, CLI, or isolated test).
"""

from __future__ import annotations

import functools
import logging
import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _locked_lazy(getter):
    """Serialize lazy initialization of a container property.

    FastAPI serves sync endpoints from worker threads, so two threads can
    otherwise both observe an unset backing field and build (and keep)
    divergent instances of the same service. A reentrant lock is required
    because some lazy properties resolve other lazy properties of the same
    container (e.g. github_service -> github_repository_store).
    """

    @functools.wraps(getter)
    def wrapper(self):
        with self._lazy_lock:
            return getter(self)

    return wrapper

_container_var: ContextVar[Any | None] = ContextVar("k41_active_container", default=None)
_active_container: Any | None = None
_default_container: Any | None = None


def get_active_container() -> Any | None:
    """Return active container from context or module-level fallback."""
    try:
        current = _container_var.get()
    except LookupError:
        current = None
    if current is not None:
        return current
    return _active_container


def set_active_container(container: Any | None) -> Any:
    """Set active container globally and in current context."""
    global _active_container
    previous = get_active_container()
    _active_container = container
    _container_var.set(container)
    return previous


def clear_active_container() -> None:
    """Clear active container and default singleton for test isolation."""
    global _active_container, _default_container
    _active_container = None
    _default_container = None
    _container_var.set(None)


def activate_context_container(container: Any | None) -> Token:
    """Activate a container for the current context only.

    Used by request middleware: the process-level fallback stays untouched
    while the current task resolves the given container. Returns a token that
    restores the previous activation via :func:`restore_context_container`.
    """
    return _container_var.set(container)


def restore_context_container(token: Token) -> None:
    """Restore container activation from an ``activate_context_container`` token."""
    _container_var.reset(token)


def require_active_container(container: Any | None = None) -> Any:
    """Return the given container, the active one, or a process default.

    On first use without an explicitly activated container, builds and
    activates a process-default container so every consumer resolves
    shared state through one container instead of module globals.
    """
    if container is not None:
        return container
    active = get_active_container()
    if active is not None:
        return active
    global _default_container
    if _default_container is None:
        _default_container = AppContainer()
        set_active_container(_default_container)
    return _default_container


def resolve_database_url(config_service: Any) -> str:
    """Pure database URL policy without global caching."""
    from sqlalchemy import make_url

    from agent.shared.infrastructure.db.engine import DEFAULT_DATABASE_URL

    config_url = ""
    get_str = getattr(config_service, "get_str", None)
    if callable(get_str):
        config_url = str(get_str("database.url", "") or "").strip()
    if not config_url:
        return DEFAULT_DATABASE_URL
    try:
        parsed = make_url(config_url)
    except Exception as exc:
        raise ValueError(f"Invalid database.url: {config_url}") from exc
    drivername = parsed.drivername or ""
    base_driver = drivername.split("+")[1] if "+" in drivername else drivername
    if base_driver in ("postgresql", "asyncpg", "psycopg2", "psycopg"):
        return config_url
    if base_driver in ("sqlite", "aiosqlite", "pysqlite"):
        raise ValueError(
            "Custom SQLite URL is not allowed in 'database.url'. "
            "Leave 'database.url' empty to use internal SQLite, "
            "or set a PostgreSQL URL."
        )
    raise ValueError(
        f"Unsupported database driver: {base_driver}. "
        "Use internal SQLite (empty 'database.url') or PostgreSQL URL."
    )


def resolve_database_type(database_url: str) -> str:
    """Normalize database type from URL without globals."""
    from sqlalchemy import make_url

    parsed = make_url(database_url)
    drivername = parsed.drivername or ""
    base_driver = drivername.split("+")[1] if "+" in drivername else drivername
    if base_driver in ("sqlite", "aiosqlite", "pysqlite"):
        return "sqlite"
    if base_driver in ("postgresql", "asyncpg", "psycopg2", "psycopg"):
        return "postgres"
    raise ValueError(
        f"Unsupported database driver: {drivername}. Use sqlite or postgresql."
    )


@dataclass
class AppContainer:
    """Own shared resources for one application lifetime."""

    bootstrap_config: Any | None = None
    config_service: Any | None = None
    database_url_override: str | None = None

    _async_engine: Any = field(default=None, repr=False)
    _async_session_maker: Any = field(default=None, repr=False)
    _tables_created: bool = field(default=False, repr=False)
    _database_url: str | None = field(default=None, repr=False)
    _checkpointer: Any = field(default=None, repr=False)
    _checkpointer_cm: Any = field(default=None, repr=False)
    _channel_manager: Any = field(default=None, repr=False)
    _channel_registry: Any = field(default=None, repr=False)
    _channel_builtins_registered: bool = field(default=False, repr=False)
    _command_registry: Any = field(default=None, repr=False)
    _telegram_webhook_runtime: Any = field(default=None, repr=False)
    _zalo_webhook_runtime: Any = field(default=None, repr=False)
    _tool_registry_service: Any = field(default=None, repr=False)
    _mcp_loaded: bool = field(default=False, repr=False)
    _mcp_service: Any = field(default=None, repr=False)
    _skill_repository: Any = field(default=None, repr=False)
    _agent_catalog_service: Any = field(default=None, repr=False)
    _agent_repository: Any = field(default=None, repr=False)
    _conversation_repository: Any = field(default=None, repr=False)
    _workspace_repository: Any = field(default=None, repr=False)
    _workspace_backend_registry: Any = field(default=None, repr=False)
    _workspace_backends_registered: bool = field(default=False, repr=False)
    _background_task_repository: Any = field(default=None, repr=False)
    _chat_stream_manager: Any = field(default=None, repr=False)
    _active_session_registry: Any = field(default=None, repr=False)
    _usage_service: Any = field(default=None, repr=False)
    _prompt_variable_service: Any = field(default=None, repr=False)
    _pairing_service: Any = field(default=None, repr=False)
    _admin_auth_service: Any = field(default=None, repr=False)
    _google_calendar_service: Any = field(default=None, repr=False)
    _google_calendar_client: Any = field(default=None, repr=False)
    _google_calendar_store: Any = field(default=None, repr=False)
    _google_oauth_manager: Any = field(default=None, repr=False)
    _extension_registry: Any = field(default=None, repr=False)
    _cache: Any = field(default=None, repr=False)
    _github_repository_store: Any = field(default=None, repr=False)
    _github_service: Any = field(default=None, repr=False)
    _task_manager: Any = field(default=None, repr=False)
    _scheduler: Any = field(default=None, repr=False)
    _scheduler_sync_engine: Any = field(default=None, repr=False)
    _provider_service: Any = field(default=None, repr=False)
    _persistence_ready: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        self._lazy_lock = threading.RLock()
        if self.config_service is None:
            from agent.shared.config.factory import create_config_service

            self.config_service = create_config_service()
        if self.bootstrap_config is None:
            from agent.bootstrap.settings import BootstrapConfig

            self.bootstrap_config = BootstrapConfig(
                host="localhost",
                port=4141,
                enable_web=True,
                enable_api=True,
                enable_dashboard=True,
            )

    @property
    def runtime_settings(self) -> Any:
        """Live RuntimeSettings derived on each access, never snapshotted."""
        return self.config_service.get_runtime_settings()

    @property
    @_locked_lazy
    def database_url(self) -> str:
        """Effective database URL scoped to this container."""
        if self.database_url_override:
            return self.database_url_override
        if self._database_url is None:
            self._database_url = resolve_database_url(self.config_service)
        return self._database_url

    @property
    def database_type(self) -> str:
        """Normalized database type scoped to this container."""
        return resolve_database_type(self.database_url)

    def invalidate_database_url(self) -> None:
        """Drop cached URL after config reload (replaces global cache clear)."""
        self._database_url = None

    @property
    @_locked_lazy
    def channel_manager(self) -> Any:
        """Lazily owned ChannelManager scoped to this container."""
        if self._channel_manager is None:
            from agent.modules.channels import ChannelManager

            self._channel_manager = ChannelManager()
        return self._channel_manager

    @property
    @_locked_lazy
    def channel_registry(self) -> Any:
        """Lazily owned ChannelRegistry scoped to this container."""
        if self._channel_registry is None:
            from agent.modules.channels import ChannelRegistry

            self._channel_registry = ChannelRegistry()
        if not self._channel_builtins_registered:
            from agent.modules.channels import BUILTIN_CHANNEL_DESCRIPTORS

            for descriptor in BUILTIN_CHANNEL_DESCRIPTORS:
                self._channel_registry.register_descriptor(descriptor, replace=True)
            self._channel_builtins_registered = True
        return self._channel_registry

    @property
    @_locked_lazy
    def command_registry(self) -> Any:
        """Lazily owned default command registry scoped to this container."""
        if self._command_registry is None:
            from agent.modules.channels import build_default_command_registry

            self._command_registry = build_default_command_registry()
        return self._command_registry

    @property
    @_locked_lazy
    def tool_registry_service(self) -> Any:
        """Lazily owned ToolRegistryService scoped to this container."""
        if self._tool_registry_service is None:
            from agent.modules.tools import BuiltinToolSource, ToolRegistryService

            service = ToolRegistryService()
            service.load_descriptors(BuiltinToolSource().load())
            self._tool_registry_service = service
        return self._tool_registry_service

    @property
    @_locked_lazy
    def mcp_service(self) -> Any:
        """Lazily owned MCPService scoped to this container."""
        if self._mcp_service is None:
            from agent.modules.mcp import ConfigMcpServerRepository, MCPService

            self._mcp_service = MCPService(repository=ConfigMcpServerRepository())
        return self._mcp_service

    @property
    @_locked_lazy
    def skill_repository(self) -> Any:
        """Lazily owned FilesystemSkillRepository scoped to this container."""
        if self._skill_repository is None:
            from agent.modules.skills import FilesystemSkillRepository

            self._skill_repository = FilesystemSkillRepository()
        return self._skill_repository

    @property
    @_locked_lazy
    def agent_catalog_service(self) -> Any:
        """Lazily owned AgentCatalogService scoped to this container."""
        if self._agent_catalog_service is None:
            from agent.modules.agents import AgentCatalogService

            self._agent_catalog_service = AgentCatalogService(repository=self.agent_repository)
        return self._agent_catalog_service

    @property
    @_locked_lazy
    def agent_repository(self) -> Any:
        """Lazily owned FilesystemAgentRepository scoped to this container."""
        if self._agent_repository is None:
            from agent.modules.agents import FilesystemAgentRepository

            self._agent_repository = FilesystemAgentRepository(None)
        return self._agent_repository

    @property
    @_locked_lazy
    def conversation_repository(self) -> Any:
        """Lazily owned ConversationThreadRepository scoped to this container."""
        if self._conversation_repository is None:
            from agent.modules.conversations import ConversationThreadRepository

            self._conversation_repository = ConversationThreadRepository()
        return self._conversation_repository

    @property
    @_locked_lazy
    def workspace_repository(self) -> Any:
        """Lazily owned ThreadWorkspaceRepository scoped to this container."""
        if self._workspace_repository is None:
            from agent.modules.workspaces import ThreadWorkspaceRepository

            self._workspace_repository = ThreadWorkspaceRepository()
        return self._workspace_repository

    @property
    @_locked_lazy
    def workspace_backend_registry(self) -> Any:
        """Lazily owned WorkspaceBackendRegistry scoped to this container."""
        if self._workspace_backend_registry is None:
            from agent.modules.workspaces import WorkspaceBackendRegistry

            self._workspace_backend_registry = WorkspaceBackendRegistry()
        if not self._workspace_backends_registered:
            from agent.modules.workspaces import BUILTIN_WORKSPACE_BACKEND_DESCRIPTORS

            for descriptor in BUILTIN_WORKSPACE_BACKEND_DESCRIPTORS:
                self._workspace_backend_registry.register(descriptor, replace=True)
            self._workspace_backends_registered = True
        return self._workspace_backend_registry

    @property
    @_locked_lazy
    def background_task_repository(self) -> Any:
        """Lazily owned BackgroundTaskRepository scoped to this container."""
        if self._background_task_repository is None:
            from agent.modules.agent_runtime import BackgroundTaskRepository

            self._background_task_repository = BackgroundTaskRepository()
        return self._background_task_repository

    @property
    @_locked_lazy
    def chat_stream_manager(self) -> Any:
        """Lazily owned ChatStreamManager scoped to this container."""
        if self._chat_stream_manager is None:
            from agent.modules.agent_runtime import ChatStreamManager

            self._chat_stream_manager = ChatStreamManager()
        return self._chat_stream_manager

    @property
    @_locked_lazy
    def active_session_registry(self) -> Any:
        """Lazily owned ActiveSessionRegistry scoped to this container."""
        if self._active_session_registry is None:
            from agent.modules.agent_runtime import ActiveSessionRegistry

            self._active_session_registry = ActiveSessionRegistry()
        return self._active_session_registry

    @property
    @_locked_lazy
    def usage_service(self) -> Any:
        """Lazily owned UsageService scoped to this container."""
        if self._usage_service is None:
            from agent.modules.usage import UsageService

            self._usage_service = UsageService()
        return self._usage_service

    @property
    @_locked_lazy
    def prompt_variable_service(self) -> Any:
        """Lazily owned PromptVariableService scoped to this container."""
        if self._prompt_variable_service is None:
            from agent.modules.prompt_variables import PromptVariableService

            self._prompt_variable_service = PromptVariableService()
        return self._prompt_variable_service

    @property
    @_locked_lazy
    def pairing_service(self) -> Any:
        """Lazily owned PairingService scoped to this container."""
        if self._pairing_service is None:
            from agent.modules.users import PairingService

            self._pairing_service = PairingService()
        return self._pairing_service

    @property
    @_locked_lazy
    def admin_auth_service(self) -> Any:
        """Lazily owned AdminAuthService scoped to this container."""
        if self._admin_auth_service is None:
            from agent.modules.admin_auth import AdminAuthService

            self._admin_auth_service = AdminAuthService()
        return self._admin_auth_service

    @property
    @_locked_lazy
    def google_calendar_client(self) -> Any:
        """Lazily owned GoogleCalendarClient scoped to this container."""
        if self._google_calendar_client is None:
            from agent.modules.google_calendar import GoogleCalendarClient

            self._google_calendar_client = GoogleCalendarClient()
        return self._google_calendar_client

    @property
    @_locked_lazy
    def google_calendar_store(self) -> Any:
        """Lazily owned GoogleCalendarStore scoped to this container."""
        if self._google_calendar_store is None:
            from agent.modules.google_calendar import GoogleCalendarStore

            self._google_calendar_store = GoogleCalendarStore()
        return self._google_calendar_store

    @property
    @_locked_lazy
    def google_oauth_manager(self) -> Any:
        """Lazily owned GoogleOAuthManager scoped to this container."""
        if self._google_oauth_manager is None:
            from agent.modules.google_calendar import GoogleOAuthManager

            self._google_oauth_manager = GoogleOAuthManager()
        return self._google_oauth_manager

    @property
    @_locked_lazy
    def google_calendar_service(self) -> Any:
        """Lazily owned GoogleCalendarService scoped to this container."""
        if self._google_calendar_service is None:
            from agent.modules.google_calendar import GoogleCalendarService

            self._google_calendar_service = GoogleCalendarService(
                client=self.google_calendar_client,
                oauth_manager=self.google_oauth_manager,
                store=self.google_calendar_store,
            )
        return self._google_calendar_service

    @property
    @_locked_lazy
    def extension_registry(self) -> Any:
        """Lazily owned ExtensionRegistry scoped to this container."""
        if self._extension_registry is None:
            from agent.shared.extension_registry import ExtensionRegistry

            self._extension_registry = ExtensionRegistry()
        return self._extension_registry

    @property
    @_locked_lazy
    def cache(self) -> Any:
        """Lazily owned InMemoryCache scoped to this container."""
        if self._cache is None:
            from agent.shared.infrastructure.cache import InMemoryCache

            self._cache = InMemoryCache()
        return self._cache

    @property
    @_locked_lazy
    def task_manager(self) -> Any:
        """Lazily owned BackgroundTaskManager scoped to this container."""
        if self._task_manager is None:
            from agent.modules.agent_runtime import BackgroundTaskManager

            self._task_manager = BackgroundTaskManager()
        return self._task_manager

    @property
    @_locked_lazy
    def github_repository_store(self) -> Any:
        """Lazily owned GitHubRepositoryStore scoped to this container."""
        if self._github_repository_store is None:
            from agent.modules.github import GitHubRepositoryStore

            self._github_repository_store = GitHubRepositoryStore()
        return self._github_repository_store

    @property
    @_locked_lazy
    def github_service(self) -> Any:
        """Lazily owned GitHubAutomationService scoped to this container."""
        if self._github_service is None:
            from agent.modules.github import GitHubAutomationService

            self._github_service = GitHubAutomationService(
                store=self.github_repository_store
            )
        return self._github_service

    @property
    @_locked_lazy
    def provider_service(self) -> Any:
        """Lazily owned ProviderService scoped to this container."""
        if self._provider_service is None:
            from agent.modules.providers import (
                AnthropicFactory,
                ConfigProviderRepository,
                GoogleFactory,
                OpenAICompatibleFactory,
                ProviderService,
                ProviderType,
            )

            repo = ConfigProviderRepository()
            service = ProviderService(repository=repo)
            service.register_factory(ProviderType.OPENAI_COMPATIBLE, OpenAICompatibleFactory())
            service.register_factory(ProviderType.GOOGLE, GoogleFactory())
            service.register_factory(ProviderType.ANTHROPIC, AnthropicFactory())
            from agent.modules.providers.internal_loader import load_internal_providers

            load_internal_providers(service, container=self)
            self._provider_service = service
        return self._provider_service

    @property
    def async_engine(self) -> Any:
        """Return initialized async engine or raise."""
        if self._async_engine is None:
            raise RuntimeError(
                "Async engine not initialized. Call 'await container.initialize_persistence()' first."
            )
        return self._async_engine

    @property
    def checkpointer(self) -> Any:
        """Return initialized checkpointer or raise."""
        if self._checkpointer is None:
            raise RuntimeError(
                "Checkpointer is not initialized. Call 'await container.initialize_persistence()' first."
            )
        return self._checkpointer

    async def initialize_persistence(self) -> None:
        """Initialize engine, database config source, migrations, checkpointer."""
        if self._persistence_ready:
            return
        from agent.shared.config.service import attach_database_config_source
        from agent.shared.infrastructure.db import Base, load_orm_models
        from agent.shared.infrastructure.db.engine import initialize_async_engine

        load_orm_models()
        await initialize_async_engine(metadata=Base.metadata, container=self)
        database_url = self.database_url
        attach_database_config_source(database_url, service=self.config_service)

        from agent.modules.agent_runtime import migrate_agent_runtime_tables
        from agent.modules.conversations import migrate_conversation_tables
        from agent.modules.github import migrate_github_tables
        from agent.modules.google_calendar import migrate_google_calendar_tables
        from agent.modules.mcp import migrate_mcp_tables
        from agent.modules.usage import prune_usage_events
        from agent.modules.workspaces import migrate_workspace_tables

        # Legacy module-level accessors (migrations, prune_usage_events) resolve
        # the active container at call time; activate this container in the
        # current context so they operate on this container's resources instead
        # of whatever container happens to be globally active.
        token = activate_context_container(self)
        try:
            migrate_workspace_tables(database_url)
            migrate_github_tables(database_url)
            migrate_mcp_tables(database_url)
            migrate_conversation_tables(database_url)
            migrate_agent_runtime_tables(database_url)
            migrate_google_calendar_tables(database_url)
            await prune_usage_events()
            await self.initialize_checkpointer()
        finally:
            restore_context_container(token)
        self._persistence_ready = True

    async def initialize_checkpointer(self) -> Any:
        """Create container-scoped LangGraph checkpointer (delegates to store)."""
        from agent.modules.workflows import initialize_checkpointer as build_checkpointer

        return await build_checkpointer(container=self)

    async def close_persistence(self) -> None:
        """Dispose container-scoped persistence resources idempotently."""
        from agent.modules.workflows import close_checkpointer
        from agent.shared.config.service import detach_database_config_source
        from agent.shared.infrastructure.db.engine import close_async_engine

        await close_checkpointer(container=self)
        await close_async_engine(container=self)
        try:
            detach_database_config_source(service=self.config_service)
        except Exception:
            logger.debug("Detach database config source failed.", exc_info=True)
        self._persistence_ready = False

    def activate(self) -> Any:
        """Set this container active and return previous for restore."""
        return set_active_container(self)

    def deactivate(self, previous: Any | None = None) -> None:
        """Restore previous active container (test isolation helper)."""
        set_active_container(previous)


def create_app_container(
    bootstrap_config: Any | None = None,
    config_service: Any | None = None,
    database_url_override: str | None = None,
) -> AppContainer:
    """Explicit factory for production containers."""
    if config_service is None:
        from agent.shared.config.factory import create_config_service

        config_service = create_config_service()
    if bootstrap_config is None:
        from agent.bootstrap.settings import load_bootstrap_config

        bootstrap_config = load_bootstrap_config(config_service)
    return AppContainer(
        bootstrap_config=bootstrap_config,
        config_service=config_service,
        database_url_override=database_url_override,
    )


def create_test_container(
    tmp_path: Path | None = None,
    database_url: str | None = None,
    bootstrap_overrides: dict[str, Any] | None = None,
) -> AppContainer:
    """Build isolated container for tests without touching globals."""
    from agent.bootstrap.settings import BootstrapConfig
    from agent.shared.config.factory import create_config_service

    config_service = create_config_service()
    if database_url is None and tmp_path is not None:
        db_file = Path(tmp_path) / "test-agent.db"
        database_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    bootstrap_kwargs: dict[str, Any] = {
        "host": "localhost",
        "port": 4141,
        "enable_web": False,
        "enable_api": True,
        "enable_dashboard": False,
    }
    if bootstrap_overrides:
        bootstrap_kwargs.update(bootstrap_overrides)
    return AppContainer(
        bootstrap_config=BootstrapConfig(**bootstrap_kwargs),
        config_service=config_service,
        database_url_override=database_url,
    )


def get_container(request: Any) -> AppContainer:
    """FastAPI dependency resolving container from request state."""
    container = getattr(getattr(request, "app", None), "state", None)
    if container is not None:
        candidate = getattr(container, "container", None)
        if candidate is not None:
            return candidate
        legacy_runtime = getattr(container, "runtime", None)
        if legacy_runtime is not None:
            nested = getattr(legacy_runtime, "container", None)
            if nested is not None:
                return nested
    active = get_active_container()
    if active is not None:
        return active
    raise RuntimeError("AppContainer is not available for this request.")


__all__ = [
    "AppContainer",
    "clear_active_container",
    "create_app_container",
    "create_test_container",
    "get_active_container",
    "get_container",
    "resolve_database_type",
    "resolve_database_url",
    "set_active_container",
]
