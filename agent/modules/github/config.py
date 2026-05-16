from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent.shared.config import get_config_service
from agent.shared.infrastructure.parsing import parse_string_or_list
from agent.shared.infrastructure.validation import is_placeholder_value


DEFAULT_TRIGGER_LABEL = "kaka-agent"
DEFAULT_MENTION_TRIGGERS = ("@kaka-agent", "/kaka")
GITHUB_WORKSPACE_ROOT = Path.home() / "kaka-agent" / "github-workspaces"


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

    @property
    def is_configured(self) -> bool:
        return (
            self.enabled
            and not is_placeholder_value(self.app_id)
            and bool(self.resolve_private_key())
            and not is_placeholder_value(self.webhook_secret)
        )

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


def get_github_settings() -> GitHubSettings:
    config = get_config_service()
    trigger_label = config.get_str("channels.github.trigger_label", DEFAULT_TRIGGER_LABEL).strip()
    if not trigger_label:
        trigger_label = DEFAULT_TRIGGER_LABEL

    return GitHubSettings(
        enabled=config.get_bool("channels.github.enabled", False),
        app_id=config.get_str("channels.github.app_id", "").strip(),
        app_slug=config.get_str("channels.github.app_slug", "").strip(),
        private_key=config.get_str("channels.github.private_key", ""),
        private_key_path=config.get_str("channels.github.private_key_path", ""),
        webhook_secret=config.get_str("channels.github.webhook_secret", "").strip(),
        default_agent=config.get_str("channels.github.default_agent", "default").strip() or "default",
        trigger_label=trigger_label,
        mention_triggers=_split_triggers(
            config.get("channels.github.mention_triggers", ",".join(DEFAULT_MENTION_TRIGGERS))
        ),
    )


__all__ = [
    "DEFAULT_MENTION_TRIGGERS",
    "DEFAULT_TRIGGER_LABEL",
    "GITHUB_WORKSPACE_ROOT",
    "GitHubSettings",
    "get_github_settings",
]
