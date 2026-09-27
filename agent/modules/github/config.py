from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from agent.shared.config.constants import DEFAULT_GITHUB_WORKSPACE_ROOT
from agent.shared.config import get_config_service
from agent.shared.infrastructure.parsing import parse_string_or_list
from agent.shared.infrastructure.validation import is_placeholder_value


DEFAULT_TRIGGER_LABEL = "k41-agent"
DEFAULT_MENTION_TRIGGERS = ("@k41-agent", "/k41")
DEFAULT_WORKSPACE_BACKEND = "local"

# Platform-managed environment variables. These identify the GitHub App itself
# and must be set by the operator (self-host). End users only install the App
# and configure per-repository bindings in the dashboard.
GITHUB_APP_ID_ENV_VAR = "GITHUB_APP_ID"
GITHUB_APP_SLUG_ENV_VAR = "GITHUB_APP_SLUG"
GITHUB_APP_PRIVATE_KEY_ENV_VAR = "GITHUB_APP_PRIVATE_KEY"
GITHUB_APP_PRIVATE_KEY_PATH_ENV_VAR = "GITHUB_APP_PRIVATE_KEY_PATH"
GITHUB_WEBHOOK_SECRET_ENV_VAR = "GITHUB_WEBHOOK_SECRET"


def _fallback_platform_value_set(env_name: str, config_key: str) -> bool:
    """Best-effort check for platform values when settings load fails.

    Mirrors _platform_value() precedence: process env first, operator YAML next.
    Placeholder values count as missing.
    """
    env_value = os.environ.get(env_name, "").strip()
    if env_value and not is_placeholder_value(env_value):
        return True
    try:
        return not is_placeholder_value(get_config_service().get_str(config_key, "").strip())
    except Exception:
        return False


def _fallback_private_key_set() -> bool:
    """Best-effort private-key check when settings load fails.

    Inline key counts when non-placeholder. A key path counts only when the
    referenced file exists and holds non-placeholder content.
    """
    if _fallback_platform_value_set(
        GITHUB_APP_PRIVATE_KEY_ENV_VAR, "channels.github.private_key"
    ):
        return True
    for env_name, config_key in (
        (GITHUB_APP_PRIVATE_KEY_PATH_ENV_VAR, "channels.github.private_key_path"),
    ):
        raw = os.environ.get(env_name, "").strip()
        candidates = [raw]
        try:
            candidates.append(get_config_service().get_str(config_key, "").strip())
        except Exception:
            pass
        for candidate in candidates:
            if is_placeholder_value(candidate):
                continue
            try:
                content = Path(os.path.expanduser(candidate)).read_text(encoding="utf-8")
            except OSError:
                continue
            if _normalize_private_key(content):
                return True
    return False


def get_github_platform_env_status() -> dict[str, bool]:
    """Report which platform values are set (without leaking values).

    Checks environment first, then the operator-owned YAML file.
    Placeholder values count as missing.
    """
    try:
        settings = get_github_settings()
        return {
            "app_id": not is_placeholder_value(settings.app_id),
            "app_slug": not is_placeholder_value(settings.app_slug),
            "private_key": bool(settings.resolve_private_key()),
            "webhook_secret": not is_placeholder_value(settings.webhook_secret),
        }
    except Exception:
        return {
            "app_id": _fallback_platform_value_set(
                GITHUB_APP_ID_ENV_VAR, "channels.github.app_id"
            ),
            "app_slug": _fallback_platform_value_set(
                GITHUB_APP_SLUG_ENV_VAR, "channels.github.app_slug"
            ),
            "private_key": _fallback_private_key_set(),
            "webhook_secret": _fallback_platform_value_set(
                GITHUB_WEBHOOK_SECRET_ENV_VAR, "channels.github.webhook_secret"
            ),
        }


def get_github_workspace_root() -> Path:
    return get_config_service().get_path(
        "workspace.github.root",
        DEFAULT_GITHUB_WORKSPACE_ROOT,
    )


@dataclass(frozen=True, slots=True)
class GitHubSettings:
    enabled: bool
    app_id: str
    app_slug: str
    private_key: str
    private_key_path: str
    webhook_secret: str
    default_agent: str
    trigger_label: str
    mention_triggers: tuple[str, ...]
    default_workspace_backend: str
    platform_managed: bool = True

    @property
    def is_configured(self) -> bool:
        return (
            self.enabled
            and not is_placeholder_value(self.app_id)
            and bool(self.resolve_private_key())
            and not is_placeholder_value(self.webhook_secret)
        )

    @property
    def platform_env_status(self) -> dict[str, bool]:
        return {
            "app_id": not is_placeholder_value(self.app_id),
            "app_slug": not is_placeholder_value(self.app_slug),
            "private_key": bool(self.resolve_private_key()),
            "webhook_secret": not is_placeholder_value(self.webhook_secret),
        }

    def missing_requirements(self) -> list[str]:
        """Human-readable list of missing platform env requirements.

        Only reports operator-owned credentials (env or config.yaml).
        The enabled flag is exposed separately via `enabled` / `is_configured`
        so the dashboard can distinguish "disabled by user" from
        "missing operator credentials".
        """
        missing: list[str] = []
        if is_placeholder_value(self.app_id):
            missing.append(
                f"{GITHUB_APP_ID_ENV_VAR} is not set (server .env or config.yaml)"
            )
        if not self.resolve_private_key():
            missing.append(
                f"{GITHUB_APP_PRIVATE_KEY_ENV_VAR} or "
                f"{GITHUB_APP_PRIVATE_KEY_PATH_ENV_VAR} is not set "
                "(server .env or config.yaml)"
            )
        if is_placeholder_value(self.webhook_secret):
            missing.append(
                f"{GITHUB_WEBHOOK_SECRET_ENV_VAR} is not set (server .env or config.yaml)"
            )
        return missing

    def resolve_private_key(self) -> str:
        inline_key = _normalize_private_key(self.private_key)
        if inline_key:
            return inline_key

        if is_placeholder_value(self.private_key_path):
            return ""

        key_path = Path(self.private_key_path).expanduser()
        try:
            return _normalize_private_key(key_path.read_text(encoding="utf-8"))
        except OSError:
            return ""


def _split_triggers(raw: object) -> tuple[str, ...]:
    if isinstance(raw, str):
        raw = raw.replace("\n", ",")
    triggers = tuple(parse_string_or_list(raw))
    return triggers or DEFAULT_MENTION_TRIGGERS


def _normalize_private_key(value: str) -> str:
    key = (value or "").strip()
    if is_placeholder_value(key):
        return ""
    return key.replace("\\n", "\n")


def _platform_value(env_name: str, config_key: str, *, expand_user: bool = False) -> str:
    """Resolve a platform value: environment first, operator YAML as fallback.

    Dashboard DB values are intentionally ignored: platform identity is never
    writable through the API. YAML is the operator-owned file next to .env.
    """
    env_value = os.environ.get(env_name, "").strip()
    if env_value:
        return os.path.expanduser(env_value) if expand_user else env_value
    try:
        file_value = get_config_service().get_str(config_key, "").strip()
    except Exception:
        file_value = ""
    if expand_user and file_value:
        return os.path.expanduser(file_value)
    return file_value


def get_github_settings() -> GitHubSettings:
    config = get_config_service()
    trigger_label = config.get_str("channels.github.trigger_label", DEFAULT_TRIGGER_LABEL).strip()
    if not trigger_label:
        trigger_label = DEFAULT_TRIGGER_LABEL

    default_backend = config.get_str(
        "channels.github.default_workspace_backend", DEFAULT_WORKSPACE_BACKEND
    ).strip()
    from agent.modules.workspaces import get_workspace_backend_registry

    if default_backend not in get_workspace_backend_registry().names():
        default_backend = DEFAULT_WORKSPACE_BACKEND

    return GitHubSettings(
        enabled=config.get_bool("channels.github.enabled", False),
        app_id=_platform_value(GITHUB_APP_ID_ENV_VAR, "channels.github.app_id"),
        app_slug=_platform_value(GITHUB_APP_SLUG_ENV_VAR, "channels.github.app_slug"),
        private_key=_platform_value(
            GITHUB_APP_PRIVATE_KEY_ENV_VAR, "channels.github.private_key"
        ),
        private_key_path=_platform_value(
            GITHUB_APP_PRIVATE_KEY_PATH_ENV_VAR,
            "channels.github.private_key_path",
            expand_user=True,
        ),
        webhook_secret=_platform_value(
            GITHUB_WEBHOOK_SECRET_ENV_VAR, "channels.github.webhook_secret"
        ),
        default_agent=config.get_str("channels.github.default_agent", "default").strip() or "default",
        trigger_label=trigger_label,
        mention_triggers=_split_triggers(
            config.get("channels.github.mention_triggers", ",".join(DEFAULT_MENTION_TRIGGERS))
        ),
        default_workspace_backend=default_backend,
    )


__all__ = [
    "DEFAULT_MENTION_TRIGGERS",
    "DEFAULT_TRIGGER_LABEL",
    "DEFAULT_WORKSPACE_BACKEND",
    "DEFAULT_GITHUB_WORKSPACE_ROOT",
    "GITHUB_APP_ID_ENV_VAR",
    "GITHUB_APP_SLUG_ENV_VAR",
    "GITHUB_APP_PRIVATE_KEY_ENV_VAR",
    "GITHUB_APP_PRIVATE_KEY_PATH_ENV_VAR",
    "GITHUB_WEBHOOK_SECRET_ENV_VAR",
    "GitHubSettings",
    "get_github_platform_env_status",
    "get_github_workspace_root",
    "get_github_settings",
]
